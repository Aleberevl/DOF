# BACKEND DOF – Archivos del Diario Oficial de la Federación

Este proyecto expone una **API REST en Flask** conectada a MySQL (`dofdb`) para:

1. **Recuperar archivos DOF**  
   - `GET /dof/files`  
   - Devuelve una lista de archivos del Diario Oficial de la Federación, junto con metadatos básicos de la publicación a la que pertenecen.

2. **Visualizar archivo completo**  
   - `GET /dof/files/{file_id}`  
   - Devuelve el detalle de un archivo específico: metadatos del archivo, sus páginas (texto e imagen) y, si existe, un resumen asociado en la tabla `summaries`.

La API implementa la especificación definida en `api-dof-files.yaml`.

---

## 1. ¿Qué hace `app.py`?

`app.py` es un backend en Flask que:

- Se conecta a MySQL usando `mysql-connector-python` con los datos de `DB_CONFIG`.
- Expone los endpoints:

### 1.1. `GET /dof/files` – Recuperar archivos DOF

- Consulta las tablas **`files`** y **`publications`**.
- Devuelve un arreglo de objetos JSON con campos como:
  - `id`, `publication_id`, `storage_uri`, `mime`, `bytes`, `sha256`, `has_ocr`, `pages_count`
  - `publication_date` (dof_date), `publication_type` (type), `source_url`

Sirve para que el cliente/Frontend pueda **listar archivos DOF** disponibles.

### 1.2. `GET /dof/files/{file_id}` – Visualizar archivo completo

- Usa **`files`** para obtener los metadatos del archivo.
- Usa **`pages`** para traer todas las páginas (`page_no`, `text`, `image_uri`).
- Usa **`summaries`** para obtener el resumen más reciente de la publicación (`object_type = 'publication'`).

Devuelve un JSON con:

- Datos del archivo.
- Arreglo de páginas con texto e imagen.
- Un `summary` de la publicación (si existe).

---

## 2. Requisitos

- Python 3.x  
- Docker (para levantar MySQL en contenedor)  
- Librerías de Python:

```bash
pip install mysql-connector-python flask flask-cors
```

---

## 3. Base de datos MySQL

### 3.1. Levantar MySQL con Docker

```bash
docker run --name mysql-container -e MYSQL_ROOT_PASSWORD=contrasena -p 3306:3306 -d mysql:latest
```

La contraseña `contrasena` debe coincidir con la que se usa en `DB_CONFIG` de `app.py`:

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

### 3.2. Crear base de datos y tablas

Para que la API funcione, necesitas la BD **dofdb** con todas las tablas del dump:

`entities, exports, files, ingestion_jobs, item_entities,
items, pages, publications, retention_queue, sections,
summaries, tasks, users`.

Lo normal es que en el proyecto tengas un archivo como **dofdb_estructura.sql** con todos los `CREATE TABLE ...`.

Tienes dos formas de cargarlo:

#### Opción A – Cargar directamente el archivo `dofdb_estructura.sql`

Desde tu máquina (donde está el archivo `.sql`):

```bash
docker exec -i mysql-container mysql -u root -pcontrasena < dofdb_estructura.sql
```

Esto crea la BD `dofdb` (si el dump la incluye) y todas las tablas.

Asegúrate de que el nombre del archivo (`dofdb_estructura.sql`) coincida con el tuyo.

#### Opción B – Entrar a MySQL y pegar el contenido del dump

Entra al contenedor MySQL:

```bash
docker exec -it mysql-container mysql -u root -pcontrasena
```

Dentro de MySQL:

```sql
CREATE DATABASE IF NOT EXISTS dofdb;
USE dofdb;
/* aquí pegas TODO el contenido del dump de estructura:
   - las sentencias CREATE TABLE ... para:
     entities, exports, files, ingestion_jobs, item_entities, items,
     pages, publications, retention_queue, sections, summaries, tasks, users
*/
```

En otras palabras: el archivo **dofdb_estructura.sql** contiene precisamente “lo que se tiene que meter a MySQL para las tablas”.  
Puedes cargarlo de una vez (Opción A) o abrirlo y copiar/pegar su contenido dentro de MySQL (Opción B).

---

### 3.3. Datos de ejemplo mínimos (para probar los endpoints)

Con la BD **dofdb** y las tablas ya creadas, inserta datos de prueba para poder probar:

- `GET /dof/files`
- `GET /dof/files/{file_id}`

Ejecuta esto dentro de MySQL:

```sql
USE dofdb;

INSERT INTO publications (id, dof_date, issue_number, type, source_url, status)
VALUES (1,'2025-11-06','10','DOF','https://dof.gob.mx/nota_detalle.php?codigo=1234567','parsed');

INSERT INTO files (id, publication_id, storage_uri, mime, bytes, sha256, has_ocr, pages_count)
VALUES (1,1,'s3://dof-storage/2025-11-06/001.pdf','application/pdf',2048000,'a2b8e8c9d1f34b8d7e42e6c8f77f74a1b2aaaaaa',1,2);

INSERT INTO pages (file_id, page_no, text, image_uri) VALUES
(1,1,'Texto extrado de la primera pgina del decreto...','https://s3.amazonaws.com/dof-storage/2025-11-06/page1.png'),
(1,2,'Texto extrado de la segunda pgina del decreto...','https://s3.amazonaws.com/dof-storage/2025-11-06/page2.png');

INSERT INTO summaries (object_type, object_id, model, summary_text, confidence)
VALUES ('publication',1,'gpt-5','Resumen del decreto: principales incentivos fiscales para PYMES.',0.95);
```

Si ves mensajes tipo:

```text
Query OK, 1 row affected
Query OK, 2 rows affected
```

significa que los datos se insertaron correctamente.

---

## 4. Ejecutar la API (`app.py`)

Con MySQL levantado y la BD `dofdb` configurada:

1. Instala las dependencias de Python:

```bash
pip install mysql-connector-python flask flask-cors
```

2. Ejecuta la app:

```bash
python app.py
```

Verás algo como:

```text
Servidor Flask iniciado. Accede a http://127.0.0.1:8000
```

---

## 5. Probar los endpoints (Postman / navegador)

### 5.1. Recuperar archivos DOF

- **Método**: `GET`  
- **URL**: `http://127.0.0.1:8000/dof/files`

Deberías ver un arreglo JSON con los archivos y sus metadatos.

### 5.2. Visualizar archivo completo

- **Método**: `GET`  
- **URL**: `http://127.0.0.1:8000/dof/files/1`

Deberías ver:

- Metadatos del archivo.
- Arreglo de `pages` con texto e imagen.
- Campo `summary` con el texto del resumen.
