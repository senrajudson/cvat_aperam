# cvat_bkp.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union

import time
import requests

from cvat_sdk.api_client import ApiClient, Configuration, models, exceptions


PathLike = Union[str, Path]


@dataclass
class CvatBkp:
    """
    Wrapper em cima do cvat-sdk, focado em:
      - about()
      - (get|get_or_create)_project
      - backup_project_zip() -> baixa zip do backup do projeto em /backups
    """
    base_url: str
    token: str
    organization: Optional[str] = None
    timeout: int = 60

    api_client: ApiClient = field(init=False)
    _org_kwargs: Dict[str, Any] = field(init=False, default_factory=dict)

    # ✅ sessão requests para downloads (result_url)
    session: requests.Session = field(init=False)

    def __post_init__(self) -> None:
        # -------- cvat-sdk client --------
        cfg = Configuration(host=self.base_url)
        cfg.api_key["tokenAuth"] = self.token
        cfg.api_key_prefix["tokenAuth"] = "Token"
        self.api_client = ApiClient(cfg)

        self._org_kwargs = {}
        if self.organization:
            self._org_kwargs = {"org": self.organization}
            self.api_client.set_default_header("X-Organization", self.organization)

        # -------- requests session (download) --------
        s = requests.Session()
        # CVAT: Authorization: Token <token>
        s.headers.update({
            "Authorization": f"Token {self.token}",
        })
        if self.organization:
            s.headers.update({"X-Organization": self.organization})

        self.session = s

    # ------------------------------------------------------------------ #
    # Helpers básicos
    # ------------------------------------------------------------------ #
    def about(self) -> Dict[str, Any]:
        data, _ = self.api_client.server_api.retrieve_about(_request_timeout=self.timeout)
        return data.to_dict() if hasattr(data, "to_dict") else dict(data)

    # ------------------------------------------------------------------ #
    # Projetos
    # ------------------------------------------------------------------ #
    def _find_project_id_by_name(self, name: str) -> Optional[int]:
        page, _ = self.api_client.projects_api.list(
            search=name,
            _request_timeout=self.timeout,
            **self._org_kwargs,
        )
        for proj in page.results or []:
            if proj.name == name:
                return int(proj.id)
        return None

    def get_project_id(self, name: str) -> int:
        pid = self._find_project_id_by_name(name)
        if pid is None:
            raise ValueError(f"Projeto '{name}' não foi encontrado")
        return pid

    def get_or_create_project(self, name: str) -> int:
        pid = self._find_project_id_by_name(name)
        if pid is not None:
            return pid

        spec = models.ProjectWriteRequest(name=name)
        proj, _ = self.api_client.projects_api.create(
            spec,
            _request_timeout=self.timeout,
            **self._org_kwargs,
        )
        return int(proj.id)

    # ------------------------------------------------------------------ #
    # BACKUP de projeto (ZIP)
    # ------------------------------------------------------------------ #
    def backup_project_zip(
        self,
        project_id: int,
        backups_dir: PathLike = "backups",
        filename: str | None = None,
        poll_seconds: float = 2.0,
        wait_timeout_seconds: int = 60 * 30,
        lightweight: bool | None = None,
        overwrite: bool = True,
    ) -> Path:
        backups_dir = Path(backups_dir)
        backups_dir.mkdir(parents=True, exist_ok=True)

        if not filename:
            filename = f"project_{project_id}_backup.zip"

        dest = backups_dir / filename

        if overwrite and dest.exists():
            dest.unlink()

        # 1) inicia export do backup
        try:
            kwargs: Dict[str, Any] = {}
            if lightweight is not None:
                kwargs["lightweight"] = lightweight

            rq, _ = self.api_client.projects_api.create_backup_export(
                project_id,
                filename=filename,
                _request_timeout=self.timeout,
                **kwargs,
            )
        except exceptions.ApiException as e:
            raise RuntimeError(
                f"Falha ao iniciar backup_export do projeto {project_id}: {e}"
            ) from e

        rq_id = getattr(rq, "rq_id", None) or (rq.get("rq_id") if isinstance(rq, dict) else None)
        if not rq_id:
            raise RuntimeError(f"Backup_export iniciou, mas não consegui ler rq_id. Retorno: {rq}")

        # 2) espera finalizar
        req_obj = self._wait_request_finished(
            rq_id=rq_id,
            poll_seconds=poll_seconds,
            timeout_seconds=wait_timeout_seconds,
        )

        result_url = getattr(req_obj, "result_url", None)
        if not result_url:
            raise RuntimeError(f"Request finished, mas veio sem result_url. rq_id={rq_id}")

        # 3) baixa zip
        self._download_result_url(result_url, dest)
        return dest

    def backup_project_zip_by_name(
        self,
        project_name: str,
        backups_dir: PathLike = "backups",
        **kwargs: Any,
    ) -> Path:
        pid = self.get_project_id(project_name)
        return self.backup_project_zip(pid, backups_dir=backups_dir, **kwargs)

    # ---------------- internal helpers ----------------
    def _wait_request_finished(
        self,
        rq_id: str,
        poll_seconds: float,
        timeout_seconds: int,
    ):
        t0 = time.time()
        last_status = None

        while True:
            req, _ = self.api_client.requests_api.retrieve(rq_id, _request_timeout=self.timeout)

            status = getattr(req, "status", None)
            status_val = getattr(status, "value", None) or str(status)

            if status_val != last_status:
                last_status = status_val

            if status_val == "finished":
                return req

            if status_val == "failed":
                msg = getattr(req, "message", None) or "Request falhou (sem mensagem)."
                raise RuntimeError(f"Backup request FAILED (rq_id={rq_id}): {msg}")

            if time.time() - t0 > timeout_seconds:
                raise TimeoutError(
                    f"Timeout esperando request (rq_id={rq_id}) finalizar. Último status={status_val}"
                )

            time.sleep(poll_seconds)

    def _download_result_url(self, result_url: str, dest: PathLike) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        url = result_url
        if url.startswith("/"):
            url = self.base_url.rstrip("/") + url

        headers = {
            "Accept": "application/zip, application/octet-stream, */*",
        }

        with self.session.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=self.timeout,
        ) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Falha ao baixar backup ({r.status_code}): {r.text}")

            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
