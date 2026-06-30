import psycopg2
import psycopg2.extras
import os

# La password e l'host vengono letti da variabili d'ambiente
# Su Render le imposteremo nel pannello di configurazione
# In locale, le mettiamo in un file .env (vedi istruzioni)

DB_HOST = os.environ.get("DB_HOST", "aws-1-eu-west-2.pooler.supabase.com")
DB_PORT = os.environ.get("DB_PORT", "6543")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres.wonrincydqejycgkcsdf")
DB_PASSWORD = os.environ.get("DB_PASSWORD")  # NESSUN default qui per sicurezza


def get_db():
    # Apre la connessione al database Supabase
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    return conn


def init_db():
    # Crea tutte le tabelle se non esistono già
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""

        CREATE TABLE IF NOT EXISTS clienti (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            indirizzo TEXT,
            partita_iva TEXT,
            codice_fiscale TEXT,
            codice_sdi TEXT,
            pec TEXT
        );

        CREATE TABLE IF NOT EXISTS prodotti (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            prezzo_base REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fatture (
            id SERIAL PRIMARY KEY,
            numero TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL,
            cliente_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            regime_iva TEXT DEFAULT '22',
            stato TEXT DEFAULT 'BOZZA',
            iban TEXT,
            totale REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS righe_fattura (
            id SERIAL PRIMARY KEY,
            fattura_id INTEGER NOT NULL,
            ddt_id INTEGER,
            descrizione TEXT,
            quantita REAL,
            prezzo REAL,
            unita_misura TEXT DEFAULT 'pz',
            totale REAL
        );

        CREATE TABLE IF NOT EXISTS ddt (
            id SERIAL PRIMARY KEY,
            fattura_id INTEGER NOT NULL,
            numero TEXT NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS righe_ddt (
            id SERIAL PRIMARY KEY,
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
    cur.close()
    conn.close()