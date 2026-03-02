/*
Builder: reads the SQLite database and generates static JSON files for the viewer.

Reads the SQLite database and generates static zstd-compressed JSON files
for the viewer. Run from the repository root:

	go run ./cmd/builder               # build everything
	go run ./cmd/builder --book romans # build only one book
	go run ./cmd/builder --clean       # delete viewer/data/static/ before building

Outputs:

	viewer/data/static/index.json.zst                      — book list with per-chapter ref counts
	viewer/data/static/bible/{book-slug}/{ch}.json.zst     — all references for a chapter
	viewer/data/static/manuscripts/{id}.json.zst           — all references from a single work
*/
package main

import (
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/klauspost/compress/zstd"
	_ "modernc.org/sqlite"
)

const maxPassageChars = 8000

var (
	repoRoot       = mustCwd()
	manuscriptsDir = filepath.Join(repoRoot, "manuscripts")
	staticDir      = filepath.Join(repoRoot, "viewer", "data", "static")
	dbPath         = filepath.Join(repoRoot, "data", "patristics.db")
)

func mustCwd() string {
	dir, err := os.Getwd()
	if err != nil {
		log.Fatalf("getting working directory: %v", err)
	}
	return dir
}

func main() {
	bookFlag := flag.String("book", "", "Only build files for this book slug")
	cleanFlag := flag.Bool("clean", false, "Delete data/static/ before building")
	flag.Parse()

	if *cleanFlag {
		os.RemoveAll(staticDir)
		fmt.Printf("Removed %s\n", staticDir)
	}

	if _, err := os.Stat(dbPath); err != nil {
		log.Fatalf("Database not found at %s. Run parser.py first.", dbPath)
	}

	// Open DB first — loadCache needs it to filter to referenced files only.
	db := openDB(dbPath)
	defer db.Close()

	cache, err := loadCache(db)
	if err != nil {
		log.Fatalf("loading manuscript cache: %v", err)
	}
	fmt.Printf("Loaded %d manuscript files into memory.\n", len(cache)/2)

	gp := buildPassages(db, cache)
	writePassages(gp)

	// All passages are interned in gp. Subsequent gp.intern() calls hit the
	// fast path without touching cache — release it before the parallel phase.
	cache = nil
	runtime.GC()

	buildAll(db, cache, *bookFlag, gp)
	buildIndex(db, *bookFlag)
	if *bookFlag == "" {
		buildWorks(db, cache, gp)
	}
	cleanupUncompressed()
}

// loadCache reads only the manuscript .txt files that are actually referenced in
// verse_refs into memory as []rune slices. Offsets stored in the DB are Python
// Unicode code point offsets (equivalent to rune indices), so we store []rune
// for O(1) slicing.
//
// Each file is indexed under two keys so both old and new filename formats work:
//   - bare filename ("mort.txt")          — used by txt-parsed manuscripts in the DB
//   - repo-root-relative path with forward slashes ("manuscripts/ccel_thml/kempis/imit.txt")
//     — used by ThML-parsed manuscripts in the DB
func loadCache(db *sql.DB) (map[string][]rune, error) {
	rows, err := db.Query(`
		SELECT DISTINCT m.filename
		FROM manuscripts m
		JOIN verse_refs vr ON vr.manuscript_id = m.id
	`)
	if err != nil {
		return nil, fmt.Errorf("querying referenced manuscripts: %w", err)
	}
	defer rows.Close()

	neededRelPaths := make(map[string]struct{})
	for rows.Next() {
		var filename string
		if err := rows.Scan(&filename); err != nil {
			return nil, fmt.Errorf("scanning manuscript filename: %w", err)
		}
		if !strings.HasPrefix(filename, "manuscripts/") {
			filename = "manuscripts/" + filename
		}
		neededRelPaths[filename] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating manuscript filenames: %w", err)
	}

	cache := make(map[string][]rune)
	err = filepath.WalkDir(manuscriptsDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".txt") {
			return nil
		}
		rel, relErr := filepath.Rel(repoRoot, path)
		if relErr != nil {
			return nil
		}
		relSlash := filepath.ToSlash(rel)
		if _, needed := neededRelPaths[relSlash]; !needed {
			return nil // skip unreferenced file
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		runes := []rune(string(data))
		cache[d.Name()] = runes
		cache[relSlash] = runes
		return nil
	})
	return cache, err
}

func openDB(path string) *sql.DB {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		log.Fatalf("opening database: %v", err)
	}
	// Allow one connection per CPU for parallel chapter queries.
	// The DB was created with WAL mode (by db.py), so concurrent reads are safe.
	db.SetMaxOpenConns(runtime.NumCPU())
	return db
}

func writeZstJSON(path string, payload any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	zw, err := zstd.NewWriter(f, zstd.WithEncoderLevel(zstd.EncoderLevelFromZstd(20)))
	if err != nil {
		return err
	}
	enc := json.NewEncoder(zw)
	enc.SetEscapeHTML(false) // match Python's ensure_ascii=False behaviour for < > &
	if err := enc.Encode(payload); err != nil {
		return err
	}
	return zw.Close()
}

func cleanupUncompressed() {
	removed := 0
	filepath.WalkDir(staticDir, func(path string, d fs.DirEntry, _ error) error {
		if d.IsDir() {
			return nil
		}
		// Remove old gzip files and any bare .json files
		if strings.HasSuffix(path, ".json.gz") || (strings.HasSuffix(path, ".json") && !strings.HasSuffix(path, ".json.zst")) {
			os.Remove(path)
			removed++
			return nil
		}
		// Remove old per-chapter bible files (bible/{slug}/{ch}.json.zst).
		// New format is bible/{slug}.json.zst (flat, no subdirectory).
		rel, err := filepath.Rel(filepath.Join(staticDir, "bible"), path)
		if err == nil && !strings.HasPrefix(rel, "..") && strings.Count(rel, string(filepath.Separator)) == 1 && strings.HasSuffix(path, ".json.zst") {
			os.Remove(path)
			removed++
		}
		return nil
	})
	// Remove empty subdirectories left behind under bible/
	bibleDir := filepath.Join(staticDir, "bible")
	if entries, err := os.ReadDir(bibleDir); err == nil {
		for _, e := range entries {
			if e.IsDir() {
				subdir := filepath.Join(bibleDir, e.Name())
				if contents, err := os.ReadDir(subdir); err == nil && len(contents) == 0 {
					os.Remove(subdir)
					removed++
				}
			}
		}
	}
	if removed > 0 {
		fmt.Printf("Cleaned up %d old/uncompressed file(s).\n", removed)
	}
}

// nullStringOr returns the string value if valid and non-empty, otherwise fallback.
// Mirrors Python's `row["field"] or "fallback"` pattern.
func nullStringOr(s sql.NullString, fallback string) string {
	if s.Valid && s.String != "" {
		return s.String
	}
	return fallback
}

// nullInt64Ptr returns a pointer to the int value, or nil if the value is NULL.
func nullInt64Ptr(n sql.NullInt64) *int {
	if !n.Valid {
		return nil
	}
	v := int(n.Int64)
	return &v
}

// nullStringPtr returns a pointer to the string value, or nil if the value is NULL.
func nullStringPtr(s sql.NullString) *string {
	if !s.Valid {
		return nil
	}
	return &s.String
}
