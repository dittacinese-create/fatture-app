import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'fatture';
    """)
    colonne = cur.fetchall()
    print("🚀 Ecco le colonne reali sul tuo database Render:")
    for col in colonne:
        print(f"- {col[0]} ({col[1]})")
except Exception as e:
    print(f"Errore di connessione: {e}")
finally:
    if 'cur' in locals(): cur.close()
    if 'conn' in locals(): conn.close()