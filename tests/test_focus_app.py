import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.core.focus_app import FocusAppController


class FocusAppControllerTests(unittest.TestCase):
    def test_validate_executable_requires_an_existing_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "reader.exe"
            executable.touch()

            self.assertEqual(
                FocusAppController.validate_executable(executable),
                executable.resolve(),
            )
            with self.assertRaises(ValueError):
                FocusAppController.validate_executable(Path(temp_dir) / "reader.txt")

    def test_focuses_an_existing_work_application_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "reader.exe"
            executable.touch()
            window = Mock()
            window.getHandle.return_value = 10
            window.getPID.return_value = 42
            window.isMinimized = False
            window.activate.return_value = True
            process = Mock()
            process.exe.return_value = str(executable)

            with (
                patch(
                    "src.core.focus_app.pywinctl.getAllWindows",
                    return_value=[window],
                ),
                patch("src.core.focus_app.psutil.pid_exists", return_value=True),
                patch("src.core.focus_app.psutil.Process", return_value=process),
            ):
                result = FocusAppController().focus_or_launch(executable)

            self.assertEqual(result, "activated")
            window.activate.assert_called_once_with(wait=True)

    def test_launches_missing_app_once_during_the_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "reader.exe"
            executable.touch()
            controller = FocusAppController()

            with (
                patch("src.core.focus_app.pywinctl.getAllWindows", return_value=[]),
                patch("src.core.focus_app.subprocess.Popen") as popen,
                patch(
                    "src.core.focus_app.time.monotonic",
                    side_effect=[100.0, 101.0],
                ),
            ):
                first_result = controller.focus_or_launch(executable)
                second_result = controller.focus_or_launch(executable)

            self.assertEqual(first_result, "launched")
            self.assertEqual(second_result, "launch_pending")
            popen.assert_called_once_with(
                [str(executable.resolve())],
                cwd=str(executable.parent),
                close_fds=True,
            )


if __name__ == "__main__":
    unittest.main()
