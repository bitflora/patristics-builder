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
	V *string `json:"v"`  // verse label, nil for chapter-level refs → JSON null
	W int64   `json:"w"`  // manuscript ID (look up in index.works)
	P int     `json:"p"`  // index into the book's passages array
}

// bookChapter holds all refs for one chapter within a book file.
type bookChapter struct {
	Ch   int       `json:"ch"`
	Refs []bookRef `json:"refs"`
}

// bookPayload is the top-level structure for data/static/bible/{slug}.json.zst.
type bookPayload struct {
	Book     string        `json:"book"`
	Passages []string      `json:"passages"`
	Chapters []bookChapter `json:"chapters"`
}

// passKey uniquely identifies a passage span within a manuscript file.
type passKey struct {
	filename string
	start    int
	end      int
}

// ── Passage extraction ────────────────────────────────────────────────────────

// readPassage slices a passage from the pre-loaded rune cache.
// start/end are Python Unicode code point offsets (rune indices).
func readPassage(cache map[string][]rune, filename string, start, end int) string {
	runes, ok := cache[filename]
	if !ok {
		return fmt.Sprintf("[source file not found: %s]", filename)
	}
	if start < 0 {
		start = 0
	}
	if end > len(runes) {
		end = len(runes)
	}
	if start > end {
		start = end
	}
	snippet := strings.TrimSpace(string(runes[start:end]))
	snippet = multiBlankRe.ReplaceAllString(snippet, "\n\n")
	sr := []rune(snippet)
	if len(sr) > maxPassageChars {
		snippet = string(sr[:maxPassageChars])
	}
	return snippet
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

// buildBook writes one book JSON.zst file containing all chapters. Returns total refs written.
func buildBook(db *sql.DB, cache map[string][]rune, bookSlug string) int {
	book, ok := bySlug[bookSlug]
	if !ok {
		return 0
	}

	rows, err := db.Query(`
		SELECT
			vr.chapter,
			vr.verse_start, vr.verse_end,
			vr.passage_start_offset, vr.passage_end_offset,
			m.id AS manuscript_id,
			m.filename
		FROM verse_refs vr
		JOIN manuscripts m ON m.id = vr.manuscript_id
		WHERE vr.book_slug = ?
		ORDER BY vr.chapter, vr.verse_start NULLS LAST, m.author, m.title
	`, bookSlug)
	if err != nil {
		log.Printf("querying book %s: %v", bookSlug, err)
		return 0
	}
	defer rows.Close()

	// Passage deduplication pool for this book.
	passIdx := make(map[passKey]int)
	var passages []string

	// Collect refs per chapter, building the passage pool as we go.
	chapterMap := make(map[int][]bookRef)
	var chapterOrder []int
	seenCh := make(map[int]bool)

	for rows.Next() {
		var chapter int
		var verseStart, verseEnd sql.NullInt64
		var passStart, passEnd int64
		var mID int64
		var filename string

		if err := rows.Scan(&chapter, &verseStart, &verseEnd, &passStart, &passEnd,
			&mID, &filename); err != nil {
			log.Printf("scanning ref row for %s: %v", bookSlug, err)
			continue
		}

		if !seenCh[chapter] {
			seenCh[chapter] = true
			chapterOrder = append(chapterOrder, chapter)
		}

		k := passKey{filename, int(passStart), int(passEnd)}
		idx, found := passIdx[k]
		if !found {
			idx = len(passages)
			passIdx[k] = idx
			passages = append(passages, readPassage(cache, filename, int(passStart), int(passEnd)))
		}

		chapterMap[chapter] = append(chapterMap[chapter], bookRef{
			V: verseLabel(verseStart, verseEnd),
			W: mID,
			P: idx,
		})
	}

	if len(chapterOrder) == 0 {
		return 0
	}

	var chapters []bookChapter
	totalRefs := 0
	for _, ch := range chapterOrder {
		refs := chapterMap[ch]
		chapters = append(chapters, bookChapter{Ch: ch, Refs: refs})
		totalRefs += len(refs)
	}

	payload := bookPayload{
		Book:     book.Name,
		Passages: passages,
		Chapters: chapters,
	}

	outPath := filepath.Join(staticDir, "bible", fmt.Sprintf("%s.json.zst", bookSlug))
	if err := writeZstJSON(outPath, payload); err != nil {
		log.Printf("writing %s: %v", outPath, err)
		return 0
	}
	return totalRefs
}

// buildAll builds all book JSON.zst files in parallel using one goroutine per book,
// bounded by a semaphore of size runtime.NumCPU().
func buildAll(db *sql.DB, cache map[string][]rune, onlyBook string) {
	slugs := queryDistinctBooks(db, onlyBook)

	sem := make(chan struct{}, runtime.NumCPU())
	var wg sync.WaitGroup
	var mu sync.Mutex
	totalRefs, totalFiles := 0, 0

	for _, slug := range slugs {
		wg.Add(1)
		go func(s string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			n := buildBook(db, cache, s)

			mu.Lock()
			if n > 0 {
				totalRefs += n
				totalFiles++
				fmt.Printf("  bible/%s.json.zst  (%d refs)\n", s, n)
			}
			mu.Unlock()
		}(slug)
	}
	wg.Wait()
	fmt.Printf("\nBuilt %d book files, %d total references.\n", totalFiles, totalRefs)
}

// ── Works building ────────────────────────────────────────────────────────────

// buildWorks writes one JSON.zst file per manuscript under data/static/manuscripts/.
func buildWorks(db *sql.DB, cache map[string][]rune) {
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
		P        int     `json:"p"` // index into passages array
	}
	type workPayload struct {
		ID       int64     `json:"id"`
		Author   string    `json:"author"`
		Title    string    `json:"title"`
		Year     *int      `json:"year"`
		CcelURL  *string   `json:"ccel_url,omitempty"`
		Passages []string  `json:"passages"`
		Refs     []workRef `json:"refs"`
	}

	totalFiles := 0
	for _, m := range manuscripts {
		refRows, err := db.Query(`
			SELECT vr.book, vr.book_slug, vr.chapter,
			       vr.verse_start, vr.verse_end,
			       vr.passage_start_offset, vr.passage_end_offset
			FROM verse_refs vr
			WHERE vr.manuscript_id = ?
			ORDER BY vr.book_slug, vr.chapter, vr.verse_start NULLS LAST
		`, m.id)
		if err != nil {
			log.Printf("querying refs for manuscript %d: %v", m.id, err)
			continue
		}

		// Passage deduplication pool for this manuscript.
		passIdx := make(map[passKey]int)
		var passages []string

		var refs []workRef
		for refRows.Next() {
			var book, bookSlug string
			var chapter int
			var verseStart, verseEnd sql.NullInt64
			var passStart, passEnd int

			if err := refRows.Scan(&book, &bookSlug, &chapter,
				&verseStart, &verseEnd, &passStart, &passEnd); err != nil {
				log.Printf("scanning ref for manuscript %d: %v", m.id, err)
				continue
			}

			k := passKey{m.filename, passStart, passEnd}
			idx, found := passIdx[k]
			if !found {
				idx = len(passages)
				passIdx[k] = idx
				passages = append(passages, readPassage(cache, m.filename, passStart, passEnd))
			}

			refs = append(refs, workRef{
				Book:     book,
				BookSlug: bookSlug,
				Chapter:  chapter,
				V:        verseLabel(verseStart, verseEnd),
				P:        idx,
			})
		}
		refRows.Close()

		if len(refs) == 0 {
			continue
		}

		payload := workPayload{
			ID:       m.id,
			Author:   nullStringOr(m.author, "Unknown"),
			Title:    nullStringOr(m.title, m.filename),
			Year:     nullInt64Ptr(m.year),
			CcelURL:  nullStringPtr(m.ccelURL),
			Passages: passages,
			Refs:     refs,
		}

		outPath := filepath.Join(worksDir, fmt.Sprintf("%d.json.zst", m.id))
		if err := writeZstJSON(outPath, payload); err != nil {
			log.Printf("writing %s: %v", outPath, err)
			continue
		}
		totalFiles++
		fmt.Printf("  manuscripts/%d.json.zst  (%d refs, %d unique passages)\n", m.id, len(refs), len(passages))
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
