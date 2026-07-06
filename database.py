import psycopg2
import psycopg2.extras
import psycopg2.pool
import os

DB_HOST = os.environ.get("DB_HOST", "aws-1-eu-west-2.pooler.supabase.com")
DB_PORT = os.environ.get("DB_PORT", "6543")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres.wonrincydqejycgkcsdf")
DB_PASSWORD = os.environ.get("DB_PASSWORD")

# Pool di connessioni: mantiene 1-5 connessioni aperte e le riusa
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    return _pool

def get_db():
    return get_pool().getconn()

def return_db(conn):
    get_pool().putconn(conn)

def init_db():
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
            prezzo_base REAL NOT NULL,
            unita_misura TEXT DEFAULT 'mq'
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
            totale REAL DEFAULT 0,
            stato_pagamento TEXT DEFAULT 'Non pagata',
            data_scadenza TEXT,
            data_pagamento TEXT,
            note TEXT
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
        CREATE TABLE IF NOT EXISTS note (
            id SERIAL PRIMARY KEY,
            titolo TEXT DEFAULT '',
            contenuto TEXT DEFAULT '',
            data_creazione TEXT DEFAULT CURRENT_DATE,
            data_modifica TEXT DEFAULT CURRENT_DATE
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
    return_db(conn)