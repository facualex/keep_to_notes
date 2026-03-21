# keep_to_enex

Convierte un export de Google Keep (vía Google Takeout) a formato `.enex` para importar directamente en **Apple Notes**.

## Características

- Acepta el `.zip` de Google Takeout directamente, sin necesidad de extraerlo
- Preserva títulos, fechas de creación y contenido de cada nota
- Convierte etiquetas de Keep en tags nativos de Apple Notes
- Mantiene checklists (como texto con ✓/☐)
- Incluye adjuntos con el mismo nombre base que la nota
- Todo el procesamiento es local — ningún dato sale de tu máquina

## Requisitos

- Python 3.8+
- `beautifulsoup4` (opcional, pero recomendado para mejor calidad de conversión)

## Instalación

```bash
git clone https://github.com/facualex/keep_to_notes.git
cd keep_to_notes
pip install -r requirements.txt
```

## Uso

**Paso 1 — Exportar desde Google Keep**

Ve a [takeout.google.com](https://takeout.google.com), haz clic en "Deseleccionar todo", marca solo **Keep** y descarga el `.zip`.

**Paso 2 — Convertir**

```bash
# Pasar el .zip directamente
python3 keep_to_enex.py takeout.zip

# O una carpeta ya extraída
python3 keep_to_enex.py carpeta_keep/

# Con nombre de salida personalizado
python3 keep_to_enex.py takeout.zip --output mis_notas.enex
```

**Paso 3 — Importar en Apple Notes**

| Dispositivo | Pasos |
|-------------|-------|
| **Mac** | Notes → `File` → `Import to Notes...` → selecciona el `.enex` |
| **iPhone / iPad** | Abre el `.enex` desde Files → toca compartir → elige Notes |

## Limitaciones conocidas

- Las imágenes referenciadas como URL externa en Keep no se transfieren
- Algunos formatos de fecha poco comunes pueden no parsearse correctamente y usar la fecha actual como fallback
- Apple Notes no soporta todas las variantes de formato HTML de Keep; el resultado puede tener diferencias menores de estilo

## Licencia

MIT
