import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

def ripristina_totali():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1. Ripristino Silvio Benedetto (N. 31 -> € 3.067,93)
        cur.execute("UPDATE fatture SET totale = 3067.93 WHERE numero = '31';")

        # 2. Ripristino Saxso S.r.l. (N. 30 -> € 43.745,78)
        cur.execute("UPDATE fatture SET totale = 43745.78 WHERE numero = '30';")

        # 3. Ripristino Alpe (N. 37 -> € 8.756,00)
        cur.execute("UPDATE fatture SET totale = 8756.00, regime_iva = 'RC' WHERE numero = '37';")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ RIPRISTINO COMPLETATO! I totali sono stati aggiornati nel DB.")
    except Exception as e:
        print(f"❌ Errore durante il ripristino: {e}")

if __name__ == "__main__":
    ripristina_totali()