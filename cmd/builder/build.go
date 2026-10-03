package main

import (
	"database/sql"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
)

var multiBlankRe = regexp.MustCompile(`\n{3,}`)

// ── JSON output types ─────────────────────────────────────────────────────────

// bookRef is a single citation record within a book file.
type bookRef struct {
	V  *string `json:"v"`            // verse label, nil for chapter-level refs → JSON null
	W  int64   `json:"w"`            // manuscript ID (look up in index.works)
	T  string  `json:"t"`            // passage text surrounding the citation
	Ca *string `json:"ca,omitempty"` // ThML scripRef id for CCEL chapter anchor (e.g. "ii-p6.1")
}

// chapterPayload is the top-level structure for data/static/bible/{slug}/{ch}.json.zst.
// Passage text is inlined so viewing a chapter costs exactly one fetch.
type chapterPayload struct {
	Book string    `json:"book"`
	Ch   int       `json:"ch"`
	Refs []bookRef `json:"refs"`
}

// passKey uniquely identifies a passage by its citation anchor within a manuscript file.
type passKey struct {
	filename       string
	citationOffset int
}

// GlobalPassages is the shared passage registry used across all build passes.
// All passages are interned during the serial buildPassages pre-pass; subsequent
// calls to intern from concurrent goroutines only hit the fast already-found path.
// The registry only exists at build time: text is inlined into each chapter and
// work file rather than shipped as one global dictionary, so the viewer never
// has to download every passage up front.
type GlobalPassages struct {
	idx      map[passKey]string // passKey → string key
	passages map[string]string  // string key → passage text
}

// text returns the passage text for a citation, interning it first if needed.
func (gp *GlobalPassages) text(cache map[string][]rune, filename string, citationOffset int) string {
	return gp.passages[gp.intern(cache, filename, citationOffset)]
}

func (gp *GlobalPassages) intern(cache map[string][]rune, filename string, citationOffset int) string {
	k := passKey{filename, citationOffset}
	if key, ok := gp.idx[k]; ok {
		return key
	}
	var text string
	var start, end int
	runes := cache[filename]
	if runes == nil {
		text = fmt.Sprintf("[source file not found: %s]", filename)
		start, end = citationOffset, citationOffset
	} else {
		start, end = expandToSentences(runes, citationOffset, 2, 3)
		// Cap the extraction range around the citation, not from start: when
		// punctuation is sparse, start can sit far enough back that capping
		// from it would cut the cited text off entirely.
		if end-start > maxPassageChars {
			start = max(start, citationOffset-maxPassageChars/2)
			end = min(end, start+maxPassageChars)
		}
		raw := strings.TrimSpace(string(runes[start:end]))
		raw = multiBlankRe.ReplaceAllString(raw, "\n\n")
		text = raw
	}
	stem := strings.TrimSuffix(filepath.Base(filename), ".txt")
	key := fmt.Sprintf("%s_%d_%d", stem, start, end)
	gp.idx[k] = key
	gp.passages[key] = text
	return key
}

// buildPassages collects all unique passages from the database into a GlobalPassages registry.
func buildPassages(db *sql.DB, cache map[string][]rune) *GlobalPassages {
	gp := &GlobalPassages{
		idx:      make(map[passKey]string),
		passages: make(map[string]string),
	}
	rows, err := db.Query(`
		SELECT DISTINCT m.filename, vr.citation_offset
		FROM verse_refs vr
		JOIN manuscripts m ON m.id = vr.manuscript_id
	`)
	if err != nil {
		log.Fatalf("querying passages: %v", err)
	}
	defer rows.Close()
	for rows.Next() {
		var filename string
		var citationOffset int
		if err := rows.Scan(&filename, &citationOffset); err != nil {
			log.Fatalf("scanning passage row: %v", err)
		}
		gp.intern(cache, filename, citationOffset)
	}
	fmt.Printf("Collected %d unique passages.\n", len(gp.passages))
	return gp
}

// ── Passage extraction ────────────────────────────────────────────────────────

// isClosingPunct returns true for punctuation that may follow sentence-ending marks.
func isClosingPunct(r rune) bool {
	switch r {
	case '"', '\'', '\u201D', '\u2019', ')', ']', '}':
		return true
	}
	return false
}

// isSentenceEnd returns true if position i in runes marks the end of a sentence.
// Sentence ends: '.', '?', '!' optionally followed by closing punct then whitespace
// or end-of-text. Newlines are NOT treated as sentence ends because both CCEL .txt
// files (80-char line-wrapped) and ThML-derived text have frequent mid-sentence
// line breaks that are formatting artifacts, not sentence boundaries.
func isSentenceEnd(runes []rune, i int) bool {
	n := len(runes)
	c := runes[i]
	if c == '.' || c == '?' || c == '!' {
		j := i + 1
		for j < n && isClosingPunct(runes[j]) {
			j++
		}
		return j >= n || runes[j] == ' ' || runes[j] == '\n' || runes[j] == '\t' || runes[j] == '\r'
	}
	return false
}

// findSentenceStartBefore scans runes backwards from pos, returning the rune index
// at the start of the sentence count sentences before pos.
// Returns 0 if fewer than count sentence boundaries exist before pos.
func findSentenceStartBefore(runes []rune, pos, count int) int {
	found := 0
	for i := pos - 1; i >= 0; i-- {
		if isSentenceEnd(runes, i) {
			found++
			if found == count {
				j := i + 1
				for j < pos && (runes[j] == ' ' || runes[j] == '\t' || runes[j] == '\r' || runes[j] == '\n') {
					j++
				}
				return j
			}
		}
	}
	return 0
}

// findSentenceEndAfter scans runes forward from pos, returning the rune index
// (exclusive) after count sentence-ending boundaries ('.', '?', '!').
// Newlines are treated as ordinary characters. Returns len(runes) if fewer
// than count boundaries exist.
func findSentenceEndAfter(runes []rune, pos, count int) int {
	n := len(runes)
	found := 0
	i := pos
	for i < n {
		c := runes[i]
		if c == '.' || c == '?' || c == '!' {
			j := i + 1
			for j < n && isClosingPunct(runes[j]) {
				j++
			}
			if j >= n || runes[j] == ' ' || runes[j] == '\n' || runes[j] == '\t' || runes[j] == '\r' {
				found++
				if found == count {
					return j
				}
			}
			i = j
		} else {
			i++
		}
	}
	return n
}

// expandToSentences returns start/end rune indices covering numBefore complete
// sentences before and numAfter complete sentences after citationOffset.
// Sentence boundaries are detected by '.', '?', '!' followed by whitespace only;
// newlines are not counted as boundaries because manuscripts use line-wrapped text.
func expandToSentences(runes []rune, citationOffset, numBefore, numAfter int) (int, int) {
	n := len(runes)
	if citationOffset < 0 {
		citationOffset = 0
	}
	if citationOffset > n {
		citationOffset = n
	}
	// Anything further than maxPassageChars from the citation is clipped
	// anyway; bounding the scans keeps sparse-punctuation texts (e.g. Nave's
	// reference lists) from being walked end to end for every citation.
	floor := max(0, citationOffset-maxPassageChars)
	ceil := min(n, citationOffset+maxPassageChars)
	start := floor + findSentenceStartBefore(runes[floor:], citationOffset-floor, numBefore)
	end := findSentenceEndAfter(runes[:ceil], citationOffset, numAfter)
	return start, end
}

// verseLabel returns the verse label string as a pointer (nil for chapter-level refs).
// Mirrors Python's _verse_label().
func verseLabel(start, end sql.NullInt64) *string {
	if !start.Valid {
		return nil
	}
	var s string
	if !end.Valid || end.Int64 == start.Int64 {
		s = strconv.FormatInt(start.Int64, 10)
	} else {
		s = fmt.Sprintf("%d-%d", start.Int64, end.Int64)
	}
	return &s
}

// ── Book building ──────────────────────────────────────────────────────────────

func queryDistinctBooks(db *sql.DB, onlyBook string) []string {
	var rows *sql.Rows
	var err error
	if onlyBook != "" {
		rows, err = db.Query(
			"SELECT DISTINCT book_slug FROM verse_refs WHERE book_slug = ? ORDER BY book_slug",
			onlyBook,
		)
	} else {
		rows, err = db.Query(
			"SELECT DISTINCT book_slug FROM verse_refs ORDER BY book_slug",
		)
	}
	if err != nil {
		log.Fatalf("querying distinct books: %v", err)
	}
	defer rows.Close()

	var result []string
	for rows.Next() {
		var slug string
		if err := rows.Scan(&slug); err != nil {
			log.Fatalf("scanning book row: %v", err)
		}
		result = append(result, slug)
	}
	return result
}

// buildBook writes one JSON.zst file per chapter under bible/{slug}/. Returns
// total refs written and, for each verse_refs.id, its index within its chapter
// file's refs array (work files point at passage text through that index).
func buildBook(db *sql.DB, cache map[string][]rune, bookSlug string, gp *GlobalPassages) (int, map[int64]int) {
	book, ok := bySlug[bookSlug]
	if !ok {
		return 0, nil
	}

	rows, err := db.Query(`
		SELECT
			vr.id,
			vr.chapter,
			vr.verse_start, vr.verse_end,
			vr.citation_offset,
			m.id AS manuscript_id,
			m.filename,
			vr.ccel_anchor
		FROM verse_refs vr
		JOIN manuscripts m ON m.id = vr.manuscript_id
		WHERE vr.book_slug = ?
		ORDER BY vr.chapter, vr.verse_start NULLS LAST, m.author, m.title, vr.id
	`, bookSlug)
	if err != nil {
		log.Printf("querying book %s: %v", bookSlug, err)
		return 0, nil
	}
	defer rows.Close()

	// Collect refs per chapter, looking up passages from the global registry.
	chapterMap := make(map[int][]bookRef)
	var chapterOrder []int
	seenCh := make(map[int]bool)
	refIndex := make(map[int64]int)

	for rows.Next() {
		var refID int64
		var chapter int
		var verseStart, verseEnd sql.NullInt64
		var citationOffset int64
		var mID int64
		var filename string
		var ccelAnchor sql.NullString

		if err := rows.Scan(&refID, &chapter, &verseStart, &verseEnd, &citationOffset,
			&mID, &filename, &ccelAnchor); err != nil {
			log.Printf("scanning ref row for %s: %v", bookSlug, err)
			continue
		}

		if !seenCh[chapter] {
			seenCh[chapter] = true
			chapterOrder = append(chapterOrder, chapter)
		}

		refIndex[refID] = len(chapterMap[chapter])
		chapterMap[chapter] = append(chapterMap[chapter], bookRef{
			V:  verseLabel(verseStart, verseEnd),
			W:  mID,
			T:  gp.text(cache, filename, int(citationOffset)),
			Ca: nullStringPtr(ccelAnchor),
		})
	}

	// Remove chapter files from a previous build so a chapter that lost all
	// its refs doesn't linger.
	bookDir := filepath.Join(staticDir, "bible", bookSlug)
	os.RemoveAll(bookDir)

	totalRefs := 0
	for _, ch := range chapterOrder {
		refs := chapterMap[ch]
		payload := chapterPayload{Book: book.Name, Ch: ch, Refs: refs}
		outPath := filepath.Join(bookDir, fmt.Sprintf("%d.json.zst", ch))
		if err := writeZstJSON(outPath, payload); err != nil {
			log.Printf("writing %s: %v", outPath, err)
			continue
		}
		totalRefs += len(refs)
	}
	return totalRefs, refIndex
}

// buildAll builds all chapter JSON.zst files in parallel using one goroutine per book,
// bounded by a semaphore of size runtime.NumCPU(). Returns the merged
// verse_refs.id → chapter-index map from buildBook.
func buildAll(db *sql.DB, cache map[string][]rune, onlyBook string, gp *GlobalPassages) map[int64]int {
	slugs := queryDistinctBooks(db, onlyBook)

	sem := make(chan struct{}, runtime.NumCPU())
	var wg sync.WaitGroup
	var mu sync.Mutex
	totalRefs, totalFiles := 0, 0
	refIndex := make(map[int64]int)

	for _, slug := range slugs {
		wg.Add(1)
		go func(s string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			n, idx := buildBook(db, cache, s, gp)

			mu.Lock()
			for id, i := range idx {
				refIndex[id] = i
			}
			if n > 0 {
				totalRefs += n
				totalFiles++
				fmt.Printf("  bible/%s/  (%d refs)\n", s, n)
			}
			mu.Unlock()
		}(slug)
	}
	wg.Wait()
	fmt.Printf("\nBuilt %d books of chapter files, %d total references.\n", totalFiles, totalRefs)
	return refIndex
}

// ── Works building ────────────────────────────────────────────────────────────

// buildWorks writes one JSON.zst file per manuscript under data/static/manuscripts/.
// Work files carry no passage text: each ref's I indexes into the refs array of
// bible/{book_slug}/{chapter}.json.zst, which the viewer fetches on demand.
// refIndex comes from buildAll.
func buildWorks(db *sql.DB, refIndex map[int64]int) {
	worksDir := filepath.Join(staticDir, "manuscripts")
	if err := os.MkdirAll(worksDir, 0755); err != nil {
		log.Fatalf("creating manuscripts dir: %v", err)
	}

	mRows, err := db.Query(
		"SELECT id, author, title, year, filename, ccel_url FROM manuscripts ORDER BY id",
	)
	if err != nil {
		log.Fatalf("querying manuscripts: %v", err)
	}

	type mRow struct {
		id       int64
		author   sql.NullString
		title    sql.NullString
		year     sql.NullInt64
		filename string
		ccelURL  sql.NullString
	}
	var manuscripts []mRow
	for mRows.Next() {
		var m mRow
		if err := mRows.Scan(&m.id, &m.author, &m.title, &m.year, &m.filename, &m.ccelURL); err != nil {
			log.Fatalf("scanning manuscript row: %v", err)
		}
		manuscripts = append(manuscripts, m)
	}
	mRows.Close()

	type workRef struct {
		Book     string  `json:"book"`
		BookSlug string  `json:"book_slug"`
		Chapter  int     `json:"chapter"`
		V        *string `json:"v"`
		I        int     `json:"i"`            // index into bible/{book_slug}/{chapter}.json.zst refs
		Ca       *string `json:"ca,omitempty"` // ThML scripRef id for CCEL chapter anchor
	}
	type workPayload struct {
		ID      int64     `json:"id"`
		Author  string    `json:"author"`
		Title   string    `json:"title"`
		Year    *int      `json:"year"`
		CcelURL *string   `json:"ccel_url,omitempty"`
		Refs    []workRef `json:"refs"`
	}

	totalFiles := 0
	for _, m := range manuscripts {
		refRows, err := db.Query(`
			SELECT vr.id, vr.book, vr.book_slug, vr.chapter,
			       vr.verse_start, vr.verse_end, vr.ccel_anchor
			FROM verse_refs vr
			WHERE vr.manuscript_id = ?
			ORDER BY vr.book_slug, vr.chapter, vr.verse_start NULLS LAST
		`, m.id)
		if err != nil {
			log.Printf("querying refs for manuscript %d: %v", m.id, err)
			continue
		}

		var refs []workRef
		for refRows.Next() {
			var refID int64
			var book, bookSlug string
			var chapter int
			var verseStart, verseEnd sql.NullInt64
			var ccelAnchor sql.NullString

			if err := refRows.Scan(&refID, &book, &bookSlug, &chapter,
				&verseStart, &verseEnd, &ccelAnchor); err != nil {
				log.Printf("scanning ref for manuscript %d: %v", m.id, err)
				continue
			}

			refs = append(refs, workRef{
				Book:     book,
				BookSlug: bookSlug,
				Chapter:  chapter,
				V:        verseLabel(verseStart, verseEnd),
				I:        refIndex[refID],
				Ca:       nullStringPtr(ccelAnchor),
			})
		}
		refRows.Close()

		if len(refs) == 0 {
			continue
		}

		payload := workPayload{
			ID:      m.id,
			Author:  nullStringOr(m.author, "Unknown"),
			Title:   nullStringOr(m.title, m.filename),
			Year:    nullInt64Ptr(m.year),
			CcelURL: nullStringPtr(m.ccelURL),
			Refs:    refs,
		}

		outPath := filepath.Join(worksDir, fmt.Sprintf("%d.json.zst", m.id))
		if err := writeZstJSON(outPath, payload); err != nil {
			log.Printf("writing %s: %v", outPath, err)
			continue
		}
		totalFiles++
		fmt.Printf("  manuscripts/%d.json.zst  (%d refs)\n", m.id, len(refs))
	}
	fmt.Printf("\nBuilt %d work files.\n", totalFiles)
}

// ── Index building ────────────────────────────────────────────────────────────

// buildIndex writes data/static/index.json.zst.
func buildIndex(db *sql.DB, onlyBook string) {
	// Per-chapter reference counts broken down by category
	chRows, err := db.Query(`
		SELECT vr.book_slug, vr.chapter, COALESCE(m.category, 'Other') AS cat, COUNT(*) AS n
		FROM verse_refs vr
		JOIN manuscripts m ON m.id = vr.manuscript_id
		GROUP BY vr.book_slug, vr.chapter, cat
		ORDER BY vr.book_slug, vr.chapter
	`)
	if err != nil {
		log.Fatalf("querying chapter counts: %v", err)
	}
	type chapterData struct {
		total int
		byCat map[string]int
	}
	chapterCounts := make(map[string]map[int]*chapterData)
	for chRows.Next() {
		var slug, cat string
		var ch, n int
		if err := chRows.Scan(&slug, &ch, &cat, &n); err != nil {
			log.Fatalf("scanning chapter count: %v", err)
		}
		if chapterCounts[slug] == nil {
			chapterCounts[slug] = make(map[int]*chapterData)
		}
		if chapterCounts[slug][ch] == nil {
			chapterCounts[slug][ch] = &chapterData{byCat: make(map[string]int)}
		}
		chapterCounts[slug][ch].total += n
		chapterCounts[slug][ch].byCat[cat] = n
	}
	chRows.Close()

	// Global works list — only include manuscripts that have at least one citation
	// so every entry in the index has a corresponding work file in data/static/manuscripts/.
	wRows, err := db.Query(`
		SELECT m.id, m.author, m.title, m.year, m.filename, m.category, m.ccel_url,
		       COUNT(vr.id) AS ref_count
		FROM manuscripts m
		JOIN verse_refs vr ON vr.manuscript_id = m.id
		GROUP BY m.id
		ORDER BY m.author, m.title
	`)
	if err != nil {
		log.Fatalf("querying global works: %v", err)
	}
	type globalWork struct {
		ID       int64   `json:"id"`
		Author   string  `json:"author"`
		Title    string  `json:"title"`
		Year     *int    `json:"year"`
		CcelURL  *string `json:"ccel_url,omitempty"`
		RefCount int     `json:"ref_count"`
		Category string  `json:"category"`
		// Refs per book slug; lets the viz tab chart citations by date without
		// fetching every work file.
		BookCounts map[string]int `json:"book_counts"`
	}
	var globalWorks []globalWork
	for wRows.Next() {
		var id int64
		var author, title, category, ccelURL sql.NullString
		var year sql.NullInt64
		var filename string
		var refCount int
		if err := wRows.Scan(&id, &author, &title, &year, &filename, &category, &ccelURL, &refCount); err != nil {
			log.Fatalf("scanning global work: %v", err)
		}
		globalWorks = append(globalWorks, globalWork{
			ID:       id,
			Author:   nullStringOr(author, "Unknown"),
			Title:    nullStringOr(title, filename),
			Year:     nullInt64Ptr(year),
			CcelURL:  nullStringPtr(ccelURL),
			RefCount: refCount,
			Category: nullStringOr(category, "Other"),
		})
	}
	wRows.Close()

	bcRows, err := db.Query(`
		SELECT manuscript_id, book_slug, COUNT(*)
		FROM verse_refs
		GROUP BY manuscript_id, book_slug
	`)
	if err != nil {
		log.Fatalf("querying per-work book counts: %v", err)
	}
	bookCounts := make(map[int64]map[string]int)
	for bcRows.Next() {
		var id int64
		var slug string
		var n int
		if err := bcRows.Scan(&id, &slug, &n); err != nil {
			log.Fatalf("scanning per-work book count: %v", err)
		}
		if bookCounts[id] == nil {
			bookCounts[id] = make(map[string]int)
		}
		bookCounts[id][slug] = n
	}
	bcRows.Close()
	for i := range globalWorks {
		globalWorks[i].BookCounts = bookCounts[globalWorks[i].ID]
	}

	type chapterEntry struct {
		Ch    int            `json:"ch"`
		Count int            `json:"count"`
		ByCat map[string]int `json:"by_cat"`
	}
	type bookEntry struct {
		Name     string         `json:"name"`
		Slug     string         `json:"slug"`
		Order    int            `json:"order"`
		Chapters []chapterEntry `json:"chapters"`
	}

	var booksOut []bookEntry
	for _, book := range books {
		if onlyBook != "" && book.Slug != onlyBook {
			continue
		}
		counts := chapterCounts[book.Slug]
		if len(counts) == 0 {
			continue
		}
		var chs []chapterEntry
		for ch := 1; ch <= book.Chapters; ch++ {
			if cd, ok := counts[ch]; ok {
				chs = append(chs, chapterEntry{Ch: ch, Count: cd.total, ByCat: cd.byCat})
			}
		}
		if len(chs) > 0 {
			booksOut = append(booksOut, bookEntry{
				Name:     book.Name,
				Slug:     book.Slug,
				Order:    book.Order,
				Chapters: chs,
			})
		}
	}

	type indexPayload struct {
		Books []bookEntry  `json:"books"`
		Works []globalWork `json:"works"`
	}
	payload := indexPayload{Books: booksOut, Works: globalWorks}

	if err := os.MkdirAll(staticDir, 0755); err != nil {
		log.Fatalf("creating static dir: %v", err)
	}
	outPath := filepath.Join(staticDir, "index.json.zst")
	if err := writeZstJSON(outPath, payload); err != nil {
		log.Fatalf("writing index: %v", err)
	}
	fmt.Printf("Wrote %s  (%d books with references)\n", outPath, len(booksOut))
}
