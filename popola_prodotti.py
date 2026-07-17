import os
import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

def popola():
    if DATABASE_URL == 'QUI_LA_TUA_STRINGA_SE_NECESSARIO' or not DATABASE_URL:
        print("Errore: Inserisci la tua stringa DATABASE_URL corretta nello script.")
        return

    print("Connessione al database PostgreSQL su Render...")
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()

    prodotti = [
        # MOSAICI / LASTRE (mq)
        ("Mosaico 1/2 misto", "mq", 8.00),
        ("Mosaico 1/2 blu", "mq", 11.00),
        ("Mosaico 2/4 misto", "mq", 6.20),
        ("Mosaico 2/4 blu", "mq", 10.00),
        ("Lose (tutte le misure)", "mq", 34.00),
        ("Losette", "mq", 30.00),

        # MATERIALI A PESO (ql)
        ("Cubetti 6/8 misto", "ql", 11.50),
        ("Cubetti 8/10 misto", "ql", 11.00),
        ("Liste 6/8 misto", "ql", 11.00),
        ("Liste 8/10 misto", "ql", 11.00),
        ("Binderi 6/8 misto", "ql", 16.00),
        ("Binderi 8/10 misto", "ql", 16.00),

        # QUADRETTONI SPESSORE 3/5 (mq)
        ("Quadrettoni 30 correre sp 3/5 misti", "mq", 36.00),
        ("Quadrettoni 30 correre sp 3/5 blu", "mq", 40.00),
        ("Quadrettoni 40 correre sp 3/5 misti", "mq", 35.00),
        ("Quadrettoni 40 correre sp 3/5 blu", "mq", 38.00),
        ("Quadrettoni 50 correre sp 3/5 misti", "mq", 35.00),
        ("Quadrettoni 50 correre sp 3/5 blu", "mq", 38.00),
        ("Quadrettoni 60 correre sp 3/5 misti", "mq", 35.00),
        ("Quadrettoni 60 correre sp 3/5 blu", "mq", 38.00),
        ("Quadrettoni 80x80 sp 3/5 misti", "mq", 35.00),
        ("Quadrettoni 80x80 sp 3/5 blu", "mq", 38.00),
        ("Quadrettoni 100x50 sp 3/5 misti", "mq", 40.00),
        ("Quadrettoni 100x50 sp 3/5 blu", "mq", 45.00),
        ("Quadrettoni 100x100 sp 3/5 misti", "pz", 80.00),

        # QUADRETTONI SPESSORE 2/3 (mq)
        ("Quadrettoni 30 correre sp 2/3 misti", "mq", 37.00),
        ("Quadrettoni 30 correre sp 2/3 blu", "mq", 40.00),
        ("Quadrettoni 40 correre sp 2/3 misti", "mq", 37.00),
        ("Quadrettoni 40 correre sp 2/3 blu", "mq", 42.00),
        ("Quadrettoni 50 correre sp 2/3 misti", "mq", 37.00),
        ("Quadrettoni 50 correre sp 2/3 blu", "mq", 42.00),
        ("Quadrettoni 60 correre sp 2/3 misti", "mq", 37.00),
        ("Quadrettoni 60 correre sp 2/3 blu", "mq", 42.00),
        ("Quadrettoni 100x50 sp 2/3 misti", "mq", 48.00)
    ]

    print("Inserimento prodotti in corso...")
    for nome, um, prezzo in prodotti:
        # Sintassi PostgreSQL per inserire solo se il prodotto non esiste già
        cursor.execute("""
            INSERT INTO prodotti (nome, um, prezzo_unitario) 
            VALUES (%s, %s, %s)
            ON CONFLICT (nome) DO NOTHING;
        """, (nome, um, prezzo))
    
    conn.commit()
    cursor.close()
    conn.close()
    print("Fatto! Tutti i prodotti sono stati inseriti correttamente nel database PostgreSQL.")

if __name__ == "__main__":
    popola()