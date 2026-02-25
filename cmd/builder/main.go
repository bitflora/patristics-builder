/*
Go replacement for src/builder.py.

Reads the SQLite database and generates static zstd-compressed JSON files
for the viewer. Run from the repository root:

  go run ./cmd/builder               # build everything
  go run ./cmd/builder --book romans # build only one book
  go run ./cmd/builder --clean       # delete data/static/ before building

Outputs:
  data/static/index.json.zst                      — book list with per-chapter ref counts
  data/static/bible/{book-slug}/{ch}.json.zst     — all references for a chapter
  data/static/manuscripts/{id}.json.zst           — all references from a single work
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
	staticDir      = filepath.Join(repoRoot, "data", "static")
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

	cache, err := loadCache()
	if err != nil {
		log.Fatalf("loading manuscript cache: %v", err)
	}
	fmt.Printf("Loaded %d manuscript files into memory.\n", len(cache))

	db := openDB(dbPath)
	defer db.Close()

	buildAll(db, cache, *bookFlag)
	buildIndex(db, *bookFlag)
	if *bookFlag == "" {
		buildWorks(db, cache)
	}
	cleanupUncompressed()
}

// loadCache reads all manuscript .txt files into memory as []rune slices.
// Offsets stored in the DB are Python Unicode code point offsets (equivalent
// to rune indices), so we store []rune for O(1) slicing.
func loadCache() (map[string][]rune, error) {
	cache := make(map[string][]rune)
	entries, err := os.ReadDir(manuscriptsDir)
	if err != nil {
		return nil, err
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".txt") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(manuscriptsDir, e.Name()))
		if err != nil {
			return nil, err
		}
		cache[e.Name()] = []rune(string(data))
	}
	return cache, nil
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
	zw, err := zstd.NewWriter(f, zstd.WithEncoderLevel(zstd.SpeedFastest))
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
		}
		return nil
	})
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
