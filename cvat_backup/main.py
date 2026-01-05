# .main.py
from __future__ import annotations

import os

from auth import User
from cvat_auth import CvatAuth
from cvat_bkp import CvatBkp


def main():
    base_url = os.getenv("CVAT_BASE_URL", "http://cvat-server:8080")
    org = os.getenv("CVAT_ORG")  # exemplo: "meioambiente" (ou None)
    project_name = os.getenv("CVAT_PROJECT", "Emissoes")
    backups_dir = os.getenv("CVAT_BACKUPS_DIR", "backups")

    # 1) login -> token
    user = User(
        username=os.getenv("CVAT_USERNAME"),
        password=os.getenv("CVAT_PASSWORD", ""),
        email=os.getenv("CVAT_EMAIL"),
    )
    token = CvatAuth(base_url).login_token(user, token_name="training-model")

    # 2) cria cliente
    cvat = CvatBkp(base_url=base_url, token=token, organization=org)

    # 3) (seu fluxo) garantir projeto existe / pegar id
    pid = cvat.get_or_create_project(project_name)
    print(f"[OK] Project '{project_name}' id={pid}")

    # ... aqui entra o resto do seu ciclo: criar task, upload TUS, etc ...

    # 4) FINAL do ciclo: backup do projeto
    zip_path = cvat.backup_project_zip(
        project_id=pid,
        backups_dir="/backups",
        # opcional:
        # lightweight=True,
        # wait_timeout_seconds=60*60,
    )
    print(f"[OK] Backup salvo em: {zip_path}")


if __name__ == "__main__":
    main()
