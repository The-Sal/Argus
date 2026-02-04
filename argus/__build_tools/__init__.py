"""

Internal Utilities that were used while building Argus, they are not intended to be used by the end user, and may be removed without warning in future releases.

"""
import time
import numpy as np
from utils3 import runAsThread

class HowLongDidThisTake:
    def __init__(self, name: str):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.started_before_finished = 0
        self.finish_times = []
        self.start_stats()

    def start(self):
        if self.start_time is not None and self.end_time is None:
            self.started_before_finished += 1
        self.start_time = time.time()

    def reset(self):
        self.start_time = None
        self.end_time = None

    def stop(self):
        if self.start_time is None:
            raise ValueError("Cannot stop before starting.")
        self.end_time = time.time()
        self.finish_times.append(self.end_time - self.start_time)

    @runAsThread
    def start_stats(self):
        while True:
            if self.start_time is not None and self.end_time is not None:
                print(f"{self.name} took {self.end_time - self.start_time:.8f} seconds to complete.")
                print(f"Started before finished: {self.started_before_finished} times.")
                # Min/Max/Average/Median and std of finish times
                if self.finish_times:
                    print(f"Min: {np.min(self.finish_times):.8f} seconds.")
                    print(f"Max: {np.max(self.finish_times):.8f} seconds.")
                    print(f"Average: {np.mean(self.finish_times):.8f} seconds.")
                    print(f"Median: {np.median(self.finish_times):.8f} seconds.")
                    print(f"Std: {np.std(self.finish_times):.8f} seconds.")
            time.sleep(1)
