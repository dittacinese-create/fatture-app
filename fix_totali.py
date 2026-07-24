import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

def ripristina_totali():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Ripristino Fatture 18 e 19 (Aprile)
        cur.execute("UPDATE fatture SET totale = 43745.78 WHERE numero = '18';")
        cur.execute("UPDATE fatture SET totale = 3067.93 WHERE numero = '19';")

        # Ripristino Fatture 30 e 31 (Maggio)
        cur.execute("UPDATE fatture SET totale = 43745.78 WHERE numero = '30';")
        cur.execute("UPDATE fatture SET totale = 3067.93 WHERE numero = '31';")

        # Allinea 'totale_pagato' = 'totale' per TUTTE le fatture segnate come PAGATA
        cur.execute("UPDATE fatture SET totale_pagato = totale WHERE LOWER(stato_pagamento) IN ('pagata', 'pagato');")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ RIPRISTINO COMPLETATO! Totali e Pagamenti allineati nel Database.")
    except Exception as e:
        print(f"❌ Errore durante il ripristino: {e}")

if __name__ == "__main__":
    ripristina_totali()