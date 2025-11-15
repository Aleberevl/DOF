# app.py
# Ejecuta con: python app.py
# Requiere:
#   pip install mysql-connector-python flask flask-cors requests

import io
import os
import zipfile
import requests
import mysql.connector
from flask import Flask, jsonify, send_file, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------------------------
# Configuración de la Conexión a la Base de Datos
# ----------------------------------------------------------------------
DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": "contrasena",   # ajusta si usaste otra
    "database": "dofdb",
    "port": 3306,
}

def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as err:
        print(f"Error al conectar a MySQL: {err}")
        return None

# ----------------------------------------------------------------------
# Utilidades para resolver/obtener el PDF desde storage_uri o download_uri
# ----------------------------------------------------------------------
def _fetch_pdf_bytes(storage_uri: str, download_uri: str | None = None) -> bytes:
    """
    Intenta obtener el PDF en este orden:
    1) Si download_uri es http(s), la usa directamente.
    2) Si storage_uri es http(s), la usa.
    3) Si storage_uri es una ruta local, la abre localmente.
    4) Si storage_uri es s3:// y no hay download_uri http(s), devolvemos NotImplemented.
    """
    url = download_uri or storage_uri

    # Caso http(s)
    if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
        return r.content

    # Caso ruta local absoluta o relativa
    if os.path.exists(storage_uri):
        with open(storage_uri, "rb") as f:
            return f.read()

    # Caso s3://... sin URL pública configurada
    if isinstance(storage_uri, str) and storage_uri.lower().startswith("s3://"):
        raise NotImplementedError(
            "storage_uri con esquema s3:// requiere 'download_uri' http(s) o presignado."
        )

    # Si nada funcionó
    raise FileNotFoundError("No fue posible resolver/obtener el PDF desde storage_uri/download_uri.")

def _safe_filename(base: str) -> str:
    # Limpia nombre para descarga
    return "".join(c for c in base if c.isalnum() or c in ("-", "_", ".", " ")).strip() or "document"

# ----------------------------------------------------------------------
# 1) GET /dof/files  -> lista de archivos + datos básicos de publicación
# ----------------------------------------------------------------------
@app.route("/dof/files", methods=["GET"])
def get_files():
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor(dictionary=True)
    sql = """
        SELECT
            f.id,
            f.publication_id,
            f.storage_uri,
            f.mime,
            f.bytes,
            f.sha256,
            f.has_ocr,
            f.pages_count,
            p.dof_date   AS publication_date,
            p.type       AS publication_type,
            p.source_url AS source_url
        FROM files f
        JOIN publications p ON f.publication_id = p.id
        ORDER BY p.dof_date DESC, f.id DESC
        LIMIT 5
    """
    # ^ Si quieres TODA la lista, quita el LIMIT 5. Así tal cual devuelve “las últimas 5 publicaciones”.

    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        for r in rows:
            r["has_ocr"] = bool(r["has_ocr"])
        return jsonify(rows), 200
    except mysql.connector.Error as err:
        return jsonify({"message": f"Error al recuperar archivos DOF: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------------------------
# 2) GET /dof/files/{file_id} -> detalle de archivo + páginas + resumen
# ----------------------------------------------------------------------
@app.route("/dof/files/<int:file_id>", methods=["GET"])
def get_file_detail(file_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor(dictionary=True)

    try:
        # Datos del archivo
        cursor.execute(
            """
            SELECT
                id,
                publication_id,
                storage_uri,
                mime,
                has_ocr
            FROM files
            WHERE id = %s
            """,
            (file_id,),
        )
        file_row = cursor.fetchone()

        if not file_row:
            return jsonify({"message": "Archivo DOF no encontrado"}), 404

        # Páginas del archivo
        cursor.execute(
            """
            SELECT
                page_no,
                text,
                image_uri
            FROM pages
            WHERE file_id = %s
            ORDER BY page_no
            """,
            (file_id,),
        )
        pages = cursor.fetchall()

        # Resumen opcional desde summaries
        cursor.execute(
            """
            SELECT summary_text
            FROM summaries
            WHERE object_type = 'publication'
              AND object_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (file_row["publication_id"],),
        )
        summary_row = cursor.fetchone()
        summary_text = summary_row["summary_text"] if summary_row else None

        result = {
            "id": file_row["id"],
            "publication_id": file_row["publication_id"],
            "storage_uri": file_row["storage_uri"],
            "mime": file_row["mime"],
            "has_ocr": bool(file_row["has_ocr"]),
            "pages": pages,
            "summary": summary_text,
        }

        return jsonify(result), 200

    except mysql.connector.Error as err:
        return jsonify({"message": f"Error al recuperar archivo DOF: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------------------------
# 3) GET /dof/files/{file_id}/download -> Descargar PDF o ZIP (PDF + summary.txt)
#    Query param opcional: bundle=zip  (por defecto entrega el PDF directo)
# ----------------------------------------------------------------------
@app.route("/dof/files/<int:file_id>/download", methods=["GET"])
def download_file(file_id):
    bundle = request.args.get("bundle", "pdf").lower().strip()  # 'pdf' | 'zip'

    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500
    cursor = conn.cursor(dictionary=True)

    try:
        # Traemos info del file + publicación, usando public_url (no download_uri)
        cursor.execute(
            """
            SELECT
                f.id,
                f.publication_id,
                f.storage_uri,
                f.public_url,              -- <--- aquí usamos public_url
                f.mime,
                p.dof_date,
                p.type AS publication_type
            FROM files f
            JOIN publications p ON f.publication_id = p.id
            WHERE f.id = %s
            """,
            (file_id,),
        )
        frow = cursor.fetchone()
        if not frow:
            return jsonify({"message": "Archivo DOF no encontrado"}), 404

        # Intentamos obtener el PDF; si hay public_url http(s), se usa primero
        try:
            pdf_bytes = _fetch_pdf_bytes(frow["storage_uri"], frow.get("public_url"))
        except NotImplementedError as nie:
            return jsonify({"message": str(nie)}), 501
        except Exception as e:
            return jsonify({"message": f"No se pudo obtener el PDF: {e}"}), 502

        # Nombre base para el archivo
        base_name = f"DOF_{frow['dof_date']}_{frow['publication_type']}_file{frow['id']}"
        base_name = _safe_filename(base_name)

        if bundle == "zip":
            # Conseguimos resumen si existe
            cursor.execute(
                """
                SELECT summary_text
                FROM summaries
                WHERE object_type = 'publication'
                  AND object_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (frow["publication_id"],),
            )
            srow = cursor.fetchone()
            summary_text = srow["summary_text"] if srow else "Sin resumen disponible."

            # Empaquetamos ZIP en memoria: document.pdf + summary.txt
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("document.pdf", pdf_bytes)
                zf.writestr("summary.txt", summary_text)
            zip_buffer.seek(0)

            return send_file(
                zip_buffer,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{base_name}.zip",
            )

        # Por defecto: PDF directo
        pdf_buffer = io.BytesIO(pdf_bytes)
        pdf_buffer.seek(0)
        mimetype = frow["mime"] or "application/pdf"
        return send_file(
            pdf_buffer,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"{base_name}.pdf",
        )

    except mysql.connector.Error as err:
        return jsonify({"message": f"Error al preparar descarga: {err}"}), 500
    finally:
        cursor.close()
        conn.close()




# ----------------------------------------------------------------------
# Rutas de la API (Endpoints CRUD)
# ----------------------------------------------------------------------

# ------------------------------------------------------
# 1. CREATE (POST) - Crear un nuevo resumen
# REQUIERE TODOS LOS CAMPOS OBLIGATORIOS
@app.route('/summaries', methods=['POST'])
def create_summary():
    data = request.get_json()
    
    # Valida que todos los campos OBLIGATORIOS estén presentes.
    required_fields = ['object_type', 'object_id', 'model', 'summary_text', 'confidence']
    missing_fields = [field for field in required_fields if field not in data]
    
    if missing_fields:
        return jsonify({"message": f"Faltan campos obligatorios: {', '.join(missing_fields)}"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor()
    
    # La consulta SQL incluye todos los campos, usando .get() para los opcionales
    sql = """
    INSERT INTO summaries (object_type, object_id, model, model_version, lang, summary_text, confidence, created_by)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    values = (
        data['object_type'],
        data['object_id'],
        data['model'],
        data.get('model_version'),
        data.get('lang', 'es'), # Usa 'es' si no se provee
        data['summary_text'],
        data['confidence'],
        data.get('created_by')
    )

    try:
        cursor.execute(sql, values)
        conn.commit()
        new_id = cursor.lastrowid
        return jsonify({"message": "Resumen creado exitosamente", "id": new_id}), 201
    except mysql.connector.Error as err:
        conn.rollback()
        # Manejo específico para error de ENUM si object_type es incorrecto
        if "Data too long for column" in str(err) or "Incorrect enum value" in str(err):
            return jsonify({"message": f"Error de dato (ENUM o longitud): {err}"}), 400
        return jsonify({"message": f"Error al crear resumen: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------
# 2. READ (GET) - Obtener todos o uno
# MUESTRA TODOS LOS CAMPOS
@app.route('/summaries', methods=['GET'])
def get_summaries():
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor(dictionary=True) # Retorna resultados como diccionarios
    
    try:
        # SELECCIONA * para asegurar que todos los campos, incluido summary_text, se muestren
        cursor.execute("SELECT * FROM summaries")
        summaries = cursor.fetchall()
        return jsonify(summaries), 200
    except mysql.connector.Error as err:
        return jsonify({"message": f"Error al leer resúmenes: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/summaries/<int:summary_id>', methods=['GET'])
def get_summary(summary_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor(dictionary=True)

    try:
        # SELECCIONA * para asegurar que todos los campos se muestren
        cursor.execute("SELECT * FROM summaries WHERE id = %s", (summary_id,))
        summary = cursor.fetchone()
        
        if summary:
            return jsonify(summary), 200
        else:
            return jsonify({"message": "Resumen no encontrado"}), 404
    except mysql.connector.Error as err:
        return jsonify({"message": f"Error al leer resumen: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------
# 3. UPDATE (PUT) - Actualizar un resumen existente
# Se mantiene la flexibilidad para actualizar solo los campos proporcionados.
@app.route('/summaries/<int:summary_id>', methods=['PUT'])
def update_summary(summary_id):
    data = request.get_json()
    
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor()
    
    # Construir la consulta de UPDATE dinámicamente con todos los campos actualizables
    fields = []
    values = []
    
    updatable_fields = ['object_type', 'object_id', 'model', 'model_version', 'lang', 'summary_text', 'confidence', 'created_by']
    
    for field in updatable_fields:
        if field in data:
            fields.append(f"{field} = %s")
            values.append(data[field])
        
    if not fields:
        return jsonify({"message": "No se proporcionaron campos para actualizar"}), 400

    sql = "UPDATE summaries SET " + ", ".join(fields) + " WHERE id = %s"
    values.append(summary_id)

    try:
        cursor.execute(sql, tuple(values))
        conn.commit()
        
        if cursor.rowcount > 0:
            return jsonify({"message": f"Resumen con ID {summary_id} actualizado"}), 200
        else:
            return jsonify({"message": f"Resumen con ID {summary_id} no encontrado o sin cambios"}), 404
    except mysql.connector.Error as err:
        conn.rollback()
        return jsonify({"message": f"Error al actualizar resumen: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------
# 4. DELETE (DELETE) - Eliminar un resumen
@app.route('/summaries/<int:summary_id>', methods=['DELETE'])
def delete_summary(summary_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"message": "Error de conexión a la base de datos"}), 500

    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM summaries WHERE id = %s", (summary_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            return jsonify({"message": f"Resumen con ID {summary_id} eliminado exitosamente"}), 200
        else:
            return jsonify({"message": f"Resumen con ID {summary_id} no encontrado"}), 404
    except mysql.connector.Error as err:
        conn.rollback()
        return jsonify({"message": f"Error al eliminar resumen: {err}"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------
# 5. READ (GET) - compartir summary + link oficial
@app.get("/summaries/{summary_id}/share")
def share_summary(summary_id: int):
    # 1) Obtener summary
    summary = db.execute("""
        SELECT 
            s.id,
            s.summary_text,
            s.object_type,
            s.object_id
        FROM summaries s
        WHERE s.id = :sid
    """, {"sid": summary_id}).fetchone()

    if summary is None:
        raise HTTPException(status_code=404, detail="Summary not found")

    # 2) Obtener el link oficial (publications.source_url)
    source_url = db.execute("""
        SELECT 
            COALESCE(p.source_url, p2.source_url, p3.source_url) AS source_url
        FROM summaries s
        LEFT JOIN publications p 
            ON (s.object_type = 'publication' AND s.object_id = p.id)
        LEFT JOIN sections sec 
            ON (s.object_type = 'section' AND s.object_id = sec.id)
        LEFT JOIN publications p2
            ON sec.publication_id = p2.id
        LEFT JOIN items i
            ON (s.object_type = 'item' AND s.object_id = i.id)
        LEFT JOIN sections sec2
            ON i.section_id = sec2.id
        LEFT JOIN publications p3
            ON sec2.publication_id = p3.id
        WHERE s.id = :sid
    """, {"sid": summary_id}).fetchone()

    return {
        "summary_id": summary.id,
        "summary_text": summary.summary_text,
        "source_url": source_url.source_url
    }





# ----------------------------------------------------------------------
# Inicialización
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("Servidor Flask iniciado. Accede a http://127.0.0.1:8000")
    app.run(port=8000, debug=True)