# Nova — Asistente local

**Versión alfa para Windows 10/11 de 64 bits y Python 3.13.**

Nova es una aplicación de escritorio que utiliza modelos locales mediante
Ollama. Incluye chat, imágenes, manuales guardados, recordatorios y un agente de
programación con aprobaciones, Stop, Undo y comprobaciones de requisitos.
El desarrollo autónomo sigue siendo experimental: abrir una ventana no
demuestra que la aplicación creada funcione correctamente.

## Instalar

1. Instala Python 3.13 y Ollama para Windows.
2. Ejecuta `Setup.cmd` desde esta carpeta, en una ubicación privada y escribible.
3. Ejecuta `ollama pull qwen3:14b` y mantén Ollama abierto.
4. Abre `Start Assistant.cmd`.

La instalación básica permite comenzar con texto. La reproducción de voz, la
escucha continua y el control del PC empiezan desactivados. Para visión,
transcripción, voces y OCR, consulta [la guía de instalación](docs/SETUP.md).
Los modelos se descargan por separado; no están incluidos en el repositorio.

## Colaborar

Puedes proponer ideas en Discussions, reportar errores en Issues y enviar
cambios mediante pull requests. Las personas responsables revisan los cambios
antes de incorporarlos. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) y
[la hoja de ruta](docs/ROADMAP.md).

## Pruebas

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe tools\run_tests.py
.venv\Scripts\python.exe tools\check_public_tree.py
```

Las pruebas utilizan datos ficticios y carpetas temporales. No requieren un
modelo de IA, micrófono ni servidor remoto. Algunas crean ventanas Tk aisladas.
Node.js 24 permite comprobar también el ejecutor de pruebas de Node.

La copia pública no contiene conversaciones, grabaciones, manuales importados,
credenciales ni configuraciones personales. Al usar Nova se crean datos locales
en la carpeta de la aplicación; no están cifrados. No los añadas a Git.

Licencia del código original: **AGPL-3.0-or-later**. Los componentes de terceros
conservan sus licencias. Consulta [los avisos](THIRD_PARTY_NOTICES.md).
