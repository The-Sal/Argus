import time
from pathlib import Path

DISABLED_PLOT = False


class PlotWriter:
    """Simple API to write y values with auto-incrementing x."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.x = 0
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create the file if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.reset()

    def write(self, y: float):
        """Write a y value. X automatically increments."""
        if not DISABLED_PLOT:
            with open(self.file_path, "a") as file:
                file.write(f"{self.x},{y}\n")
                file.flush()
            self.x += 1

    def reset(self):
        """Clear file and reset x to 0."""
        with open(self.file_path, "w") as file:
            file.write("clear\n")
        self.x = 0


# Example usage
if __name__ == "__main__":
    writer = PlotWriter("/Users/Salman/Library/Containers/SVO-Productions.plotview/Data/tmp/plot.plt")

    # Write some points
    writer.write(50)  # x=0, y=50
    writer.write(75)  # x=1, y=75
    writer.write(25)  # x=2, y=25

    # Reset when needed
    # writer.reset()  # Clears file, x back to 0

    # Write more points
    writer.write(60)  # x=0, y=60