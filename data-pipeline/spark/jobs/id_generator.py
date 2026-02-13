# spark/utils/id_generator.py
from spark.utils.postgres_connector import get_postgres_conn

def get_start_seq(brand_name: str, batch_count: int) -> int:
    """
    브랜드별 product_id 시작 번호를 안전하게 발급
    """

    conn = get_postgres_conn()
    cur = conn.cursor()

    sql = """
        UPDATE brand_sequences
        SET last_seq = last_seq + %s
        WHERE brand_name = %s
        RETURNING last_seq - %s + 1;
    """

    cur.execute(sql, (batch_count, brand_name.upper(), batch_count))
    start_seq = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return start_seq
