# BACKEND DOF – API Flask + MySQL (dofdb)

Este proyecto expone una **API REST en Flask** conectada a MySQL (`dofdb`) para tres casos de uso:

1) **Recuperar archivos DOF (últimas 5 publicaciones)**  
   - **GET** `/dof/files`  
   - Lista de archivos con metadatos de su publicación.

2) **Visualizar archivo completo**  
   - **GET** `/dof/files/{file_id}`  
   - Metadatos del archivo + sus páginas (`pages`) + resumen más reciente si existe (`summaries`).

3) **Descargar PDF o ZIP (PDF + resumen)**  
   - **GET** `/dof/files/{file_id}/download`  
   - Parámetro opcional: `bundle=zip` para recibir un `.zip` con `document.pdf` y `summary.txt`.  
   - La descarga resuelve primero `files.public_url` (si es `http(s)`), luego intenta `files.storage_uri` (ruta local). Si `storage_uri` es `s3://` debes proporcionar una URL pública/presignada en `public_url`.

La API sigue la especificación funcional definida en `api-dof-files.yaml` y está pensada para integrarse con pipelines de ingestión/OCR y NLP.

---

## 1) Requisitos

- Python 3.x  
- Docker (para MySQL en contenedor)  
- Librerías de Python:
  ```bash
  pip install mysql-connector-python flask flask-cors requests
  ```

---

## 2) Levantar MySQL en Codespaces (o local con Docker)

1. **Crear/arrancar contenedor MySQL (puerto 3306):**
   ```bash
   docker run --name mysql-container -e MYSQL_ROOT_PASSWORD=contrasena -p 3306:3306 -d mysql:latest
   ```

2. **Crear la base `dofdb` y cargar la estructura (archivo seguro):**
   - Guarda el dump **SAFE** como `dofdb_estructura.sql` (el que incluye `public_url` en `files` y evita SETs problemáticos).
   - Ejecuta:
   ```bash
   # Crea la base si no existe
   docker exec -i mysql-container mysql -u root -pcontrasena -e "CREATE DATABASE IF NOT EXISTS dofdb;"
   # Carga la estructura (CREATE TABLE ...)
   docker exec -i mysql-container mysql -u root -pcontrasena dofdb < dofdb_estructura.sql
   ```

3. **(Opcional) Entrar a MySQL interactivo:**
   ```bash
   docker exec -it mysql-container mysql -u root -pcontrasena
   ```

> **DB_CONFIG** en `app.py` debe tener la misma contraseña/host/puerto:
```python
DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": "contrasena",
    "database": "dofdb",
    "port": 3306,
}
```

---

## 3) Insertar datos de ejemplo (mínimos para probar los 3 endpoints)

**Importante:** Para probar `/download`, usa **una URL pública** en `public_url` o un **PDF local existente** en `storage_uri`.

### Opción A — Usar una URL pública (rápido)
```sql
USE dofdb;

INSERT INTO publications (id, dof_date, issue_number, type, source_url, status)
VALUES (1,'2025-11-06','10','DOF','https://dof.gob.mx/nota_detalle.php?codigo=1234567','parsed')
ON DUPLICATE KEY UPDATE dof_date=VALUES(dof_date);

INSERT INTO files (id, publication_id, storage_uri, public_url, mime, bytes, sha256, has_ocr, pages_count)
VALUES (
  1, 1,
  's3://dof-storage/2025-11-06/001.pdf',
  'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf',
  'application/pdf', 2048000, 'a2b8e8c9d1f34b8d7e42e6c8f77f74a1b2aaaaaa', 1, 2
)
ON DUPLICATE KEY UPDATE publication_id=VALUES(publication_id);

INSERT INTO pages (file_id, page_no, text, image_uri) VALUES
(1,1,'Texto extraído de la primera página...','https://s3.amazonaws.com/dof-storage/2025-11-06/page1.png'),
(1,2,'Texto extraído de la segunda página...','https://s3.amazonaws.com/dof-storage/2025-11-06/page2.png')
ON DUPLICATE KEY UPDATE text=VALUES(text);

INSERT INTO summaries (object_type, object_id, model, summary_text, confidence)
VALUES ('publication',1,'gpt-5','Resumen del decreto: principales incentivos fiscales para PYMES.',0.95);


USE dofdb;

-- =========================================================================
-- PASO 1: INSERTAR LA PUBLICACIÓN PRINCIPAL
-- =========================================================================

-- La publicación corresponde al Diario Oficial del 4 de noviembre de 2025.
INSERT INTO publications (id, dof_date, issue_number, type, source_url, sha256, published_at, fetched_at, status)
VALUES (
    1001, -- ID arbitrario
    '2025-11-04',
    '296/2025', -- De la primera página del PDF
    'DOF',      -- Diario Oficial de la Federación
    'https://dof.gob.mx/nota_detalle.php?fecha=2025-11-04', -- URL de ejemplo
    'e8f7a6b9c2d1e0f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7', -- SHA256 ficticio
    '2025-11-04 00:00:00',
    NOW(),
    'parsed' -- El estado es 'parsed' (analizado) ya que tenemos las páginas.
)
ON DUPLICATE KEY UPDATE dof_date=VALUES(dof_date), status=VALUES(status);


-- =========================================================================
-- PASO 2: INSERTAR EL ARCHIVO (PDF) ASOCIADO
-- =========================================================================

-- El archivo es el PDF que acabamos de revisar.
INSERT INTO files (id, publication_id, storage_uri, public_url, mime, bytes, sha256, has_ocr, pages_count)
VALUES (
    2001, -- ID arbitrario
    1001,
    '/workspaces/DOFDB/04112025-MAT.pdf', -- Ruta local simulada
    'https://dof.gob.mx/pdfs/2025/NOV/04112025-MAT.pdf', -- URL pública de ejemplo
    'application/pdf',
    5120000, -- Tamaño de archivo ficticio (5MB)
    'f3e9d0c1b8a7f6e5d4c3b2a109876543210fedcba9876543210fedcba987654321', -- SHA256 ficticio
    1,       -- has_ocr = 1 (Sí tiene OCR, ya que el texto fue extraído)
    340      -- Páginas contadas al final del fragmento del PDF
)
ON DUPLICATE KEY UPDATE publication_id=VALUES(publication_id);


-- =========================================================================
-- PASO 3: INSERTAR LAS PÁGINAS EXTRAÍDAS (Información extendida con más de 100 caracteres)
-- =========================================================================

-- Página 1: Portada y Contenido Principal (Texto extendido)
INSERT INTO pages (file_id, page_no, text, image_uri)
VALUES (
    2001,
    1,
    'DIARIO OFICIAL DE LA FEDERACION. ÓRGANO DEL GOBIERNO CONSTITUCIONAL DE LOS ESTADOS UNIDOS MEXICANOS. No. de publicación: 296/2025. Ciudad de México, martes 4 de noviembre de 2025. CONTENIDO: Secretaría de Gobernación, Secretaría de Hacienda y Crédito Público, Secretaría de Bienestar, Secretaría de Medio Ambiente y Recursos Naturales, y otras dependencias cruciales para la administración federal.',
    NULL
),
-- Página 2: Índice de la Secretaría de Gobernación (Texto extendido)
(
    2001,
    2,
    '2 DIARIO OFICIAL INDICE SECRETARIA DE GOBERNACION. Aviso por el que se da a conocer el extracto de la solicitud de registro constitutivo como asociación religiosa de una entidad interna de Convención Nacional Bautista de México, A.R., denominada Convención Bautista de la Región Carbonífera en Coahuila, iniciando el proceso en la página 5. Este es un documento importante del Poder Ejecutivo Federal.',
    NULL
)
ON DUPLICATE KEY UPDATE text=VALUES(text);


-- =========================================================================
-- PASO 4: SIMULAR UNA TAREA DE PROCESAMIENTO (E.g., Tarea de Resumen)
-- =========================================================================

-- Se añade una tarea de resumen para la publicación, indicando que está en cola.
INSERT INTO tasks (publication_id, task_type, status)
VALUES (
    1001,
    'summarize',
    'queued'
)
ON DUPLICATE KEY UPDATE status=VALUES(status);


-- =========================================================================
-- PASO 5: SIMULAR UN RESUMEN GENERADO (Para el índice/publicación completa)
-- =========================================================================

-- El resumen se genera sobre la publicación completa (objeto tipo 'publication').
INSERT INTO summaries (object_type, object_id, model, summary_text, confidence)
VALUES (
    'publication',
    1001,
    'gemini-2.5-flash-preview-09-2025', -- Modelo de resumen de ejemplo
    'El Diario Oficial del 4 de noviembre de 2025 contiene avisos de la Secretaría de Gobernación sobre registros de asociaciones religiosas y diversos acuerdos de la SHCP, Bienestar y Energía, con un enfoque en normativas y trámites administrativos.',
    0.8950
)
ON DUPLICATE KEY UPDATE summary_text=VALUES(summary_text);



```

### Opción B — Usar un PDF local
1. Copia un PDF al workspace (ej. `./data/ejemplo.pdf`).  
2. Actualiza la fila:
```sql
UPDATE files
SET public_url = NULL,
    storage_uri = '/workspaces/DOFDB_ac/data/ejemplo.pdf'
WHERE id = 1;
```

---

## 4) Ejecutar la API (Flask)

En tu Codespace/terminal del repo:

```bash
pip install mysql-connector-python flask flask-cors requests
python app.py
```

- En Codespaces se abrirá la **Port 8000**. Pulsa **Open in Browser** cuando aparezca la notificación.  
- Localmente, ve a: <http://127.0.0.1:8000>

---

## 5) Probar los 3 endpoints (con `curl`)

> Si usas navegador, basta con abrir las URLs. Con `curl`, además puedes guardar archivos.

### 5.1) Últimas 5 publicaciones (lista)
```bash
curl -s http://127.0.0.1:8000/dof/files | python -m json.tool
```

### 5.2) Detalle de un archivo (metadatos + páginas + summary)
```bash
curl -s http://127.0.0.1:8000/dof/files/1 | python -m json.tool
```

### 5.3) Descargar PDF (por defecto) o ZIP (PDF + summary.txt)

- **PDF directo:**
```bash
curl -L -o dof_1.pdf "http://127.0.0.1:8000/dof/files/1/download"
```

- **ZIP con PDF + resumen:**
```bash
curl -L -o dof_1_bundle.zip "http://127.0.0.1:8000/dof/files/1/download?bundle=zip"
```

> Si obtienes un ZIP vacío o error, revisa que `public_url` sea una URL `http(s)` válida **o** que `storage_uri` apunte a un PDF **existente** y legible desde el servidor.

---

## 6) Notas finales y buenas prácticas

- `public_url` simplifica la descarga (ideal con pre-signed URLs). Si sólo usas `s3://...`, el backend necesita una URL pública o presignada.  
- Para producción, agrega autenticación, logging y maneja límites/paginación en `/dof/files`.  
- Índices útiles ya incluidos: `idx_publications_dof_date`, `idx_files_pub_date`, `uq_pages_file_page`.

---

