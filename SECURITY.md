# Política de seguridad

## Reportar una vulnerabilidad

Abre un issue privado en **Security → Report a vulnerability** (informes de
vulnerabilidad privados activados). Si no está disponible, un issue normal
marcando la etiqueta `security`, sin detalles explotables públicos.

## Superficie de este proyecto, dicha honestamente

- El binario publicado **no está firmado**. SmartScreen avisará; lo que sí se
  garantiza es la **integridad**: cada release lleva `CHECKSUMS.txt` con los
  SHA256 del zip y del exe (`certutil -hashfile Magisys-vX.Y.Z-win64.zip SHA256`).
- La inferencia va a **nubes de terceros** (g4f sin clave, Groq con clave).
  No envíes secretos en los prompts si eso te preocupa: el texto viaja a esos
  proveedores tal cual.
- La clave de Groq vive en `magi/data/groq_key.txt` **por decisión del
  propietario del repositorio** (el repo alterna público/privado). Esa clave
  da acceso a la cuota de subagentes de la cuenta asociada. Para rotarla:
  cambia el fichero, `set GROQ_API_KEY=...` (el entorno siempre gana), y
  regenera en Groq Console. Un binario publicado con clave vieja dentro
  sigue sirviendo a quien lo tenga: rota y re-publica.
- `GROQ_MODELS` permite cambiar los modelos sin recompilar: si un nombre de
  modelo muere, esa variable lo sustituye sin tocar código.

## Auditoría automática

- `pip-audit` sobre `requirements.txt` y `npm audit --omit=dev` sobre la GUI,
  ambos **bloqueantes** en CI.
- Dependabot (seguridad) activado para pip y npm.
