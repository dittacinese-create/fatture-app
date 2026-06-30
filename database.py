import sqlite3

def get_db():
    conn = sqlite3.connect("fatture.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS clienti (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        indirizzo TEXT,
        partita_iva TEXT,
        codice_fiscale TEXT,
        codice_sdi TEXT,
        pec TEXT
    );

    CREATE TABLE IF NOT EXISTS prodotti (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        prezzo_base REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fatture (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT UNIQUE NOT NULL,
        data TEXT NOT NULL,
        cliente_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        regime_iva TEXT DEFAULT '22',
        stato TEXT DEFAULT 'BOZZA',
        iban TEXT
    );

    CREATE TABLE IF NOT EXISTS righe_fattura (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fattura_id INTEGER NOT NULL,
        ddt_id INTEGER,
        descrizione TEXT,
        quantita REAL,
        prezzo REAL,
        unita_misura TEXT DEFAULT 'pz',
        totale REAL
    );

    CREATE TABLE IF NOT EXISTS ddt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fattura_id INTEGER NOT NULL,
        numero TEXT NOT NULL,
        data TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS righe_ddt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ddt_id INTEGER NOT NULL,
        prodotto_id INTEGER,
        descrizione TEXT,
        quantita REAL,
        prezzo REAL,
        unita_misura TEXT DEFAULT 'pz',
        totale REAL
    );
    """)

    conn.commit()
    conn.close()