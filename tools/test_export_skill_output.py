import os
import tempfile
from pathlib import Path

from tools.export_skill_output import export


def test_export() -> None:
    with tempfile.TemporaryDirectory() as directory:
        os.environ["MODA_OUTPUT_DIR"] = directory
        path = export("完整分析", "000001", "平安/银行")
        assert path.parent == Path(directory)
        assert "平安_银行_000001" in path.name
        assert path.read_text(encoding="utf-8-sig") == "完整分析"


if __name__ == "__main__":
    test_export()
    print("ok")
