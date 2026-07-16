import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Interroghiamo la definizione del vincolo 'fatture_tipo_check'
    cur.execute("""
        SELECT pg_get_constraintdef(oid) 
        FROM pg_constraint 
        WHERE conname = 'fatture_tipo_check';
    """)
    
    risultato = cur.fetchone()
    if risultato:
        print("🚀 Definizione del vincolo trovata:")
        print(risultato[0])
    else:
        print("❌ Vincolo 'fatture_tipo_check' non trovato. Proviamo a cercare tutti i vincoli CHECK...")
        cur.execute("""
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE contype = 'c';
        """)
        for r in cur.fetchall():
            print(f"- {r[0]}: {r[1]}")
            
except Exception as e:
    print(f"Errore: {e}")
finally:
    if 'cur' in locals(): cur.close()
    if 'conn' in locals(): conn.close()