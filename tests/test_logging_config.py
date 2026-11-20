import logging
import tempfile
import unittest
from pathlib import Path

from src.core.logging_config import configure_logging


class LoggingConfigTests(unittest.TestCase):
    def test_configure_logging_writes_a_rotating_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = configure_logging(Path(temp_dir))
            logging.getLogger("mindshield.test").warning("backup failed")
            root = logging.getLogger()
            for handler in list(root.handlers):
                handler.flush()
            self.assertEqual(log_path, Path(temp_dir) / "mindshield.log")
            self.assertIn("backup failed", log_path.read_text(encoding="utf-8"))
            for handler in list(root.handlers):
                if getattr(handler, "baseFilename", None) == str(log_path):
                    root.removeHandler(handler)
                    handler.close()


if __name__ == "__main__":
    unittest.main()
