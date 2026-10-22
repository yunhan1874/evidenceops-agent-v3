from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class Whitelist:
    def __init__(self, external_ids: set[str] | None = None, names: set[str] | None = None) -> None:
        self.external_ids = external_ids or set()
        self.names = names or set()

    @classmethod
    def from_excel(cls, path: Path | None) -> Whitelist:
        if path is None or not path.exists():
            logger.warning("未配置白名单 Excel；仅建议在开发环境使用")
            return cls()
        frame = pd.read_excel(path, header=1, engine="openpyxl")
        columns = {str(column).strip(): column for column in frame.columns}
        id_column = columns.get("用户 ID") or columns.get("用户ID")
        name_column = columns.get("姓名") or columns.get("员工姓名")
        external_ids = set()
        names = set()
        if id_column is not None:
            external_ids = {
                str(value).strip()
                for value in frame[id_column].dropna().tolist()
                if str(value).strip()
            }
        if name_column is not None:
            names = {
                str(value).split("|")[0].strip()
                for value in frame[name_column].dropna().tolist()
                if str(value).strip()
            }
        logger.info("白名单载入完成：%s 个用户 ID，%s 个姓名", len(external_ids), len(names))
        return cls(external_ids=external_ids, names=names)

    def allows(
        self, external_user_id: str, display_name: str, *, allow_empty: bool = False
    ) -> bool:
        if not self.external_ids and not self.names:
            return allow_empty
        return external_user_id in self.external_ids or display_name in self.names
