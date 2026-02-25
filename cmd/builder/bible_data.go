package main

// Book holds metadata for a single book of the Bible.
type Book struct {
	Name     string
	Slug     string
	Order    int
	Chapters int
}

// books contains all canonical Bible books in canonical order.
// Ported from src/bible_data.py — only the fields needed by the builder.
var books = []Book{
	// ── Old Testament ──────────────────────────────────────────────────────────
	{"Genesis", "genesis", 1, 50},
	{"Exodus", "exodus", 2, 40},
	{"Leviticus", "leviticus", 3, 27},
	{"Numbers", "numbers", 4, 36},
	{"Deuteronomy", "deuteronomy", 5, 34},
	{"Joshua", "joshua", 6, 24},
	{"Judges", "judges", 7, 21},
	{"Ruth", "ruth", 8, 4},
	{"1 Samuel", "1-samuel", 9, 31},
	{"2 Samuel", "2-samuel", 10, 24},
	{"1 Kings", "1-kings", 11, 22},
	{"2 Kings", "2-kings", 12, 25},
	{"1 Chronicles", "1-chronicles", 13, 29},
	{"2 Chronicles", "2-chronicles", 14, 36},
	{"Ezra", "ezra", 15, 10},
	{"Nehemiah", "nehemiah", 16, 13},
	{"Esther", "esther", 17, 10},
	{"Job", "job", 18, 42},
	{"Psalms", "psalms", 19, 150},
	{"Proverbs", "proverbs", 20, 31},
	{"Ecclesiastes", "ecclesiastes", 21, 12},
	{"Song of Solomon", "song-of-solomon", 22, 8},
	{"Isaiah", "isaiah", 23, 66},
	{"Jeremiah", "jeremiah", 24, 52},
	{"Lamentations", "lamentations", 25, 5},
	{"Ezekiel", "ezekiel", 26, 48},
	{"Daniel", "daniel", 27, 12},
	{"Hosea", "hosea", 28, 14},
	{"Joel", "joel", 29, 3},
	{"Amos", "amos", 30, 9},
	{"Obadiah", "obadiah", 31, 1},
	{"Jonah", "jonah", 32, 4},
	{"Micah", "micah", 33, 7},
	{"Nahum", "nahum", 34, 3},
	{"Habakkuk", "habakkuk", 35, 3},
	{"Zephaniah", "zephaniah", 36, 3},
	{"Haggai", "haggai", 37, 2},
	{"Zechariah", "zechariah", 38, 14},
	{"Malachi", "malachi", 39, 4},
	// ── Deuterocanon / Apocrypha ───────────────────────────────────────────────
	{"Tobit", "tobit", 40, 14},
	{"Judith", "judith", 41, 16},
	{"1 Maccabees", "1-maccabees", 42, 16},
	{"2 Maccabees", "2-maccabees", 43, 15},
	{"3 Maccabees", "3-maccabees", 44, 7},
	{"4 Maccabees", "4-maccabees", 45, 18},
	{"Wisdom of Solomon", "wisdom", 46, 19},
	{"Sirach", "sirach", 47, 51},
	{"Baruch", "baruch", 48, 6},
	{"Letter of Jeremiah", "letter-of-jeremiah", 49, 1},
	{"Prayer of Azariah", "prayer-of-azariah", 50, 1},
	{"Susanna", "susanna", 51, 1},
	{"Bel and the Dragon", "bel", 52, 1},
	{"Prayer of Manasseh", "prayer-of-manasseh", 53, 1},
	{"1 Esdras", "1-esdras", 54, 9},
	{"2 Esdras", "2-esdras", 55, 16},
	// ── New Testament ─────────────────────────────────────────────────────────
	{"Matthew", "matthew", 56, 28},
	{"Mark", "mark", 57, 16},
	{"Luke", "luke", 58, 24},
	{"John", "john", 59, 21},
	{"Acts", "acts", 60, 28},
	{"Romans", "romans", 61, 16},
	{"1 Corinthians", "1-corinthians", 62, 16},
	{"2 Corinthians", "2-corinthians", 63, 13},
	{"Galatians", "galatians", 64, 6},
	{"Ephesians", "ephesians", 65, 6},
	{"Philippians", "philippians", 66, 4},
	{"Colossians", "colossians", 67, 4},
	{"1 Thessalonians", "1-thessalonians", 68, 5},
	{"2 Thessalonians", "2-thessalonians", 69, 3},
	{"1 Timothy", "1-timothy", 70, 6},
	{"2 Timothy", "2-timothy", 71, 4},
	{"Titus", "titus", 72, 3},
	{"Philemon", "philemon", 73, 1},
	{"Hebrews", "hebrews", 74, 13},
	{"James", "james", 75, 5},
	{"1 Peter", "1-peter", 76, 5},
	{"2 Peter", "2-peter", 77, 3},
	{"1 John", "1-john", 78, 5},
	{"2 John", "2-john", 79, 1},
	{"3 John", "3-john", 80, 1},
	{"Jude", "jude", 81, 1},
	{"Revelation", "revelation", 82, 22},
}

// bySlug maps a book slug to its Book metadata.
var bySlug = func() map[string]Book {
	m := make(map[string]Book, len(books))
	for _, b := range books {
		m[b.Slug] = b
	}
	return m
}()
