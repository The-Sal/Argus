"""
This file aims to automatically bridge the gap between versions of Argus.
"""
import os
import pickle

def pf(*args, **kwargs):
    print('[{}]'.format(__name__), *args, **kwargs)

class PolymarketCustomUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if "py_clob_client" in module :
            # Dynamically create a stub class named after whatever pickle asks for
            pf(f"Creating stub for missing module {module}, class {name}")
            magic_type = type(name, (), {
                "__init__": lambda self, *a, **k: None,
                "__setstate__": lambda self, state: self.__dict__.update(state),
                "__repr__": lambda self: f"<Stub {name} {self.__dict__}>",
            })
            # set an attribute to be able to recongise we made this fake class
            # so we can drop it later
            setattr(magic_type, "_is_stub", True)
            return magic_type
        return super().find_class(module, name)

class PickleFixer:
    def __init__(self, pickle_file: str = "~/.argus/capital_cache.pkl"):
        self.pickle_file = os.path.expanduser(pickle_file)
        self._solutions = {
            'py_clob_client': self.py_clob_client_patch,
        }

    @staticmethod
    def py_clob_client_patch(file_path):
        """
        This function fixes module errors of 'py_clob_client' which was dropped in Argus 0.3.0
        :return:
        """

        with open(file_path, 'rb') as f:
            load = PolymarketCustomUnpickler(f).load()
            popped = load['polymarket_direct'].pop('_create_or_derive_api_creds:():{}', None)
            pf(f"Popped {popped} from pickle file during py_clob_client patch")
            pf(f"Remaining keys in polymarket_direct: {load['polymarket_direct'].keys()}")
            pf('Remaining keys in cache: ', load.keys())

        tmp_file = file_path + '.tmp'
        with open(tmp_file, 'wb') as f:
            pickle.dump(load, f)

        # re-load the pickle file
        with open(tmp_file, 'rb') as f:
            _ = pickle.load(f)

        os.rename(tmp_file, file_path)

    def run_diagnostics(self):
        pf("Running diagnostics on pickle file:", self.pickle_file)
        try:
            with open(self.pickle_file, 'rb') as f:
                _ = pickle.load(f)
            pf("Pickle file loaded successfully. No issues detected.")
            return
        except ModuleNotFoundError as e:
            pf("ModuleNotFoundError detected while loading pickle file:", e)
            missing_module = e.name
            pf('Checking for solutions...')
            if missing_module in self._solutions:
                pf(f"Solution found for {missing_module}. Attempting to apply...")
                self._solutions[missing_module](self.pickle_file)
            else:
                raise ModuleNotFoundError(f"No solution found for module {missing_module}.")

        with open(self.pickle_file, 'rb') as f:
            _ = pickle.load(f)

        pf("Pickle file loaded successfully. Patching complete.")

    @property
    def cache_version(self):
        """
        Returns the version of the cache file

        Error codes:
        'nv' = No version found
        'fnf' = File not found
        'corrupted' = Corrupted file (unpickling error or EOFError)

        :return:
        """
        try:
            with open(self.pickle_file, 'rb') as f:
                content = pickle.load(f)
                return content.get('argus_version', 'nv')
        except FileNotFoundError:
            return 'fnf'
        except (EOFError, pickle.UnpicklingError):
            return 'corrupted'


if __name__ == '__main__':
    # pf = PickleFixer()
    # pf(pf.cache_version)
    # pf.run_diagnostics()
    pass
