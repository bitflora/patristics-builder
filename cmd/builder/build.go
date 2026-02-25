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

// workEntry is a manuscript entry in a chapter's deduplicated works list.
type workEntry struct {
	ID       int    `json:"id"`
	Author   string `json:"author"`
	Title    string `json:"title"`
	Year     *int   `json:"year"`
	Filename string `json:"filename"`
}

// chapterRef is a single citation record within a chapter file.
type chapterRef struct {
	V    *string `json:"v"`    // verse label, nil for chapter-level refs → JSON null
	W    int     `json:"w"`    // index into the chapter's works list
	Text string  `json:"text"` // extracted passage text
}

// chapterPayload is the top-level structure for data/static/bible/{slug}/{ch}.json.gz.
type chapterPayload struct {
	Book    string       `json:"book"`
	Chapter int          `json:"chapter"`
	Works   []workEntry  `json:"works"`
	Refs    []chapterRef `json:"refs"`
}

// chapterKey identifies a distinct (book_slug, chapter) pair.
type chapterKey struct {
	slug    string
	chapter int
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

// ── Chapter building ──────────────────────────────────────────────────────────

func queryDistinctChapters(db *sql.DB, onlyBook string) []chapterKey {
	var rows *sql.Rows
	var err error
	if onlyBook != "" {
		rows, err = db.Query(
			"SELECT DISTINCT book_slug, chapter FROM verse_refs WHERE book_slug = ? ORDER BY book_slug, chapter",
			onlyBook,
		)
	} else {
		rows, err = db.Query(
			"SELECT DISTINCT book_slug, chapter FROM verse_refs ORDER BY book_slug, chapter",
		)
	}
	if err != nil {
		log.Fatalf("querying distinct chapters: %v", err)
	}
	defer rows.Close()

	var result []chapterKey
	for rows.Next() {
		var ck chapterKey
		if err := rows.Scan(&ck.slug, &ck.chapter); err != nil {
			log.Fatalf("scanning chapter row: %v", err)
		}
		result = append(result, ck)
	}
	return result
}

// buildChapter writes one chapter JSON.gz file. Returns the number of refs written.
func buildChapter(db *sql.DB, cache map[string][]rune, bookSlug string, chapter int) int {
	book, ok := bySlug[bookSlug]
	if !ok {
		return 0
	}

	rows, err := db.Query(`
		SELECT
			vr.verse_start, vr.verse_end,
			vr.passage_start_offset, vr.passage_end_offset,
			m.id AS manuscript_id,
			m.filename, m.author, m.title, m.year
		FROM verse_refs vr
		JOIN manuscripts m ON m.id = vr.manuscript_id
		WHERE vr.book_slug = ? AND vr.chapter = ?
		ORDER BY vr.verse_start NULLS LAST, m.author, m.title
	`, bookSlug, chapter)
	if err != nil {
		log.Printf("querying chapter %s %d: %v", bookSlug, chapter, err)
		return 0
	}
	defer rows.Close()

	worksSeen := make(map[int64]int)
	var worksList []workEntry
	var refs []chapterRef

	for rows.Next() {
		var verseStart, verseEnd sql.NullInt64
		var passStart, passEnd int64
		var mID int64
		var filename string
		var author, title sql.NullString
		var year sql.NullInt64

		if err := rows.Scan(&verseStart, &verseEnd, &passStart, &passEnd,
			&mID, &filename, &author, &title, &year); err != nil {
			log.Printf("scanning ref row for %s %d: %v", bookSlug, chapter, err)
			continue
		}

		if _, seen := worksSeen[mID]; !seen {
			worksSeen[mID] = len(worksList)
			worksList = append(worksList, workEntry{
				ID:       len(worksList),
				Author:   nullStringOr(author, "Unknown"),
				Title:    nullStringOr(title, filename),
				Year:     nullInt64Ptr(year),
				Filename: filename,
			})
		}
		workIdx := worksSeen[mID]

		text := readPassage(cache, filename, int(passStart), int(passEnd))
		refs = append(refs, chapterRef{
			V:    verseLabel(verseStart, verseEnd),
			W:    workIdx,
			Text: text,
		})
	}

	if len(refs) == 0 {
		return 0
	}

	payload := chapterPayload{
		Book:    book.Name,
		Chapter: chapter,
		Works:   worksList,
		Refs:    refs,
	}

	outPath := filepath.Join(staticDir, "bible", bookSlug, fmt.Sprintf("%d.json.gz", chapter))
	if err := writeGzJSON(outPath, payload); err != nil {
		log.Printf("writing %s: %v", outPath, err)
		return 0
	}
	return len(refs)
}

// buildAll builds all chapter JSON.gz files in parallel using one goroutine per chapter,
// bounded by a semaphore of size runtime.NumCPU().
func buildAll(db *sql.DB, cache map[string][]rune, onlyBook string) {
	chapters := queryDistinctChapters(db, onlyBook)

	sem := make(chan struct{}, runtime.NumCPU())
	var wg sync.WaitGroup
	var mu sync.Mutex
	totalRefs, totalFiles := 0, 0

	for _, ck := range chapters {
		wg.Add(1)
		go func(slug string, ch int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			n := buildChapter(db, cache, slug, ch)

			mu.Lock()
			if n > 0 {
				totalRefs += n
				totalFiles++
				fmt.Printf("  bible/%s/%d.json.gz  (%d refs)\n", slug, ch, n)
			}
			mu.Unlock()
		}(ck.slug, ck.chapter)
	}
	wg.Wait()
	fmt.Printf("\nBuilt %d chapter files, %d total references.\n", totalFiles, totalRefs)
}

// ── Works building ────────────────────────────────────────────────────────────

// buildWorks writes one JSON.gz file per manuscript under data/static/manuscripts/.
func buildWorks(db *sql.DB, cache map[string][]rune) {
	worksDir := filepath.Join(staticDir, "manuscripts")
	if err := os.MkdirAll(worksDir, 0755); err != nil {
		log.Fatalf("creating manuscripts dir: %v", err)
	}

	mRows, err := db.Query(
		"SELECT id, author, title, year, filename FROM manuscripts ORDER BY id",
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
	}
	var manuscripts []mRow
	for mRows.Next() {
		var m mRow
		if err := mRows.Scan(&m.id, &m.author, &m.title, &m.year, &m.filename); err != nil {
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
		Text     string  `json:"text"`
	}
	type workPayload struct {
		ID       int64     `json:"id"`
		Author   string    `json:"author"`
		Title    string    `json:"title"`
		Year     *int      `json:"year"`
		Filename string    `json:"filename"`
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

			text := readPassage(cache, m.filename, passStart, passEnd)
			refs = append(refs, workRef{
				Book:     book,
				BookSlug: bookSlug,
				Chapter:  chapter,
				V:        verseLabel(verseStart, verseEnd),
				Text:     text,
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
			Filename: m.filename,
			Refs:     refs,
		}

		outPath := filepath.Join(worksDir, fmt.Sprintf("%d.json.gz", m.id))
		if err := writeGzJSON(outPath, payload); err != nil {
			log.Printf("writing %s: %v", outPath, err)
			continue
		}
		totalFiles++
		fmt.Printf("  manuscripts/%d.json.gz  (%d refs)\n", m.id, len(refs))
	}
	fmt.Printf("\nBuilt %d work files.\n", totalFiles)
}

// ── Index building ────────────────────────────────────────────────────────────

// buildIndex writes data/static/index.json.gz.
func buildIndex(db *sql.DB, onlyBook string) {
	// Per-chapter reference counts across all books
	chRows, err := db.Query(`
		SELECT book_slug, chapter, COUNT(*) AS n
		FROM verse_refs
		GROUP BY book_slug, chapter
		ORDER BY book_slug, chapter
	`)
	if err != nil {
		log.Fatalf("querying chapter counts: %v", err)
	}
	chapterCounts := make(map[string]map[int]int)
	for chRows.Next() {
		var slug string
		var ch, n int
		if err := chRows.Scan(&slug, &ch, &n); err != nil {
			log.Fatalf("scanning chapter count: %v", err)
		}
		if chapterCounts[slug] == nil {
			chapterCounts[slug] = make(map[int]int)
		}
		chapterCounts[slug][ch] = n
	}
	chRows.Close()

	// Global works list with total ref counts
	wRows, err := db.Query(`
		SELECT m.id, m.author, m.title, m.year, m.filename,
		       COUNT(vr.id) AS ref_count
		FROM manuscripts m
		LEFT JOIN verse_refs vr ON vr.manuscript_id = m.id
		GROUP BY m.id
		ORDER BY m.author, m.title
	`)
	if err != nil {
		log.Fatalf("querying global works: %v", err)
	}
	type globalWork struct {
		ID       int64  `json:"id"`
		Author   string `json:"author"`
		Title    string `json:"title"`
		Year     *int   `json:"year"`
		RefCount int    `json:"ref_count"`
	}
	var globalWorks []globalWork
	for wRows.Next() {
		var id int64
		var author, title sql.NullString
		var year sql.NullInt64
		var filename string
		var refCount int
		if err := wRows.Scan(&id, &author, &title, &year, &filename, &refCount); err != nil {
			log.Fatalf("scanning global work: %v", err)
		}
		globalWorks = append(globalWorks, globalWork{
			ID:       id,
			Author:   nullStringOr(author, "Unknown"),
			Title:    nullStringOr(title, filename),
			Year:     nullInt64Ptr(year),
			RefCount: refCount,
		})
	}
	wRows.Close()

	type chapterEntry struct {
		Ch    int `json:"ch"`
		Count int `json:"count"`
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
			if n, ok := counts[ch]; ok {
				chs = append(chs, chapterEntry{Ch: ch, Count: n})
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
	outPath := filepath.Join(staticDir, "index.json.gz")
	if err := writeGzJSON(outPath, payload); err != nil {
		log.Fatalf("writing index: %v", err)
	}
	fmt.Printf("Wrote %s  (%d books with references)\n", outPath, len(booksOut))
}
