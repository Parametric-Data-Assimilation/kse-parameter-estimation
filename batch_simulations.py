# batch_simulations.py

import numpy as np
from functools import partial
import json
import traceback
import itertools
from copy import deepcopy
import os
import shutil
import sys
from tqdm import tqdm
import multiprocessing as mp

from KS_order import KS, KSAssim

def fourier_projector(spec, modes=21):
    mod_spec = spec.copy()
    mod_spec[modes:] = 0
    return np.fft.irfft(mod_spec)

def l2_norm(x):
    return (np.sum(np.abs(x)**2)) ** .5

# This needs to not be a class method so it can be used with the multiprocessing library
def run_simulation(initial_guess={'lambda2': 2}, mu=1, dt=.01,
    alpha=None, max_t=10, modes=21, alpha_scale=None, mu_scale=None,
    order=2, timestepper='rk3', lambda2=1, N=512, start_xspec=None, start_x=None):
        # Initialize true solution from common starting point
        true = KS(dt=dt, N=N, lambda2=lambda2, timestepper=timestepper)
        if start_x is not None and start_xspec is not None:
            true.xspec = start_xspec.copy()
            true.x = start_x.copy()

        estimate_params = list(initial_guess.keys())
        projector = partial(fourier_projector, modes=modes)
        assimilator = KSAssim(projector, N=N, mu=mu, alpha=alpha,
            dt=dt, timestepper=timestepper, order=order, estimate_params=estimate_params, **initial_guess)
        max_n = int(max_t/dt)
        interp_errors = []
        true_errors = []
        param_errors = {p : [] for p in estimate_params}

        for n in range(max_n):
            target = projector(true.xspec)
            projected_state = projector(assimilator.xspec)
            interp_errors.append(l2_norm(target-projected_state))
            true_errors.append(assimilator.error(true))

            for p in param_errors:
                param_errors[p].append(np.abs(getattr(assimilator, p)-getattr(true,p)))
            assimilator.set_target(target)
            assimilator.advance()
            true.advance()

        result = {p: np.array(arr) for p,arr in param_errors.items()}
        result.update({'interp_errors': np.array(interp_errors), 'true_errors': np.array(true_errors)})
        return result

def simulation_wrapper(params, **kwargs):
    try:
        return run_simulation(**params, **kwargs)
    except Exception as e:
        message = traceback.format_exc()
        print(params, message)
        return None

class BatchSimulator(object):
    """A class to run batch simulations of the KSE equations
    """

    def __init__(self, outdir, lambda2=1, N=256, warmup_time=10, warmup_dt=1e-4, overwrite=False):
        """Run normal KSE for 10 seconds to get into the chaotic realm.
        """

        warmup = KS(lambda2=lambda2, N=N, dt=warmup_dt)
        for i in range(int(warmup_time/warmup_dt)):
            warmup.advance()

        self.start_x = warmup.x
        self.start_xspec = warmup.xspec
        self.outdir=outdir
        self.N = N
        self.lambda2 = lambda2
        # Make the directory - we don't tolerate using the same directory twice
        if os.path.exists(outdir):
            if not overwrite:
                ans = input(f"Directory {outdir} already exists! Remove [Y/N]? ")
                if ans.strip().lower() != "y":
                    print("Aborting!")
                    sys.exit(0)
            shutil.rmtree(outdir)
        os.makedirs(outdir)
        print("Initialized the chaotic initial state.")

    def run_batch(self, base_params, ranges={}, grid=True, n_jobs=None):
        if not len(ranges):
            raise RuntimeWarning("No parameter ranges specified - nothing to do!")
            return

        param_list = []
        params_to_vary = list(ranges.keys())
        if 'mu_scale' in base_params and 'mu' in ranges:
            raise ValueError("Cannot both set mu_scale and vary mu!")
        if 'alpha_scale' in base_params and 'alpha' in ranges:
            raise ValueError("Cannot both set alpha_scale and vary alpha!")

        for choice in itertools.product(*[ranges[p] for p in params_to_vary]):
            input_params = deepcopy(base_params)
            for c, p in zip(choice, params_to_vary):
                input_params[p] = c

            if 'dt' in input_params:
                if 'mu_scale' in input_params:
                    input_params['mu'] = input_params['mu_scale'] / input_params['dt']

                if 'alpha_scale' in input_params:
                    input_params['alpha'] = input_params['alpha_scale'] / input_params['dt']

            param_list.append(input_params)
        print(param_list)
        self.run_simulations_low(param_list)

    def run_simulations_low(self, param_list, n_jobs=None):
        index = []
        index_file = f"{self.outdir}/index.json"
        if n_jobs is None:
            # Run at 75% capacity by default
            n_jobs = (3*mp.cpu_count() // 4)

        n_simulations = len(param_list)
        processes = min(max(1, n_jobs), n_simulations)
        pool = mp.Pool(processes=processes)
        func = partial(simulation_wrapper, start_xspec=self.start_xspec, start_x=self.start_x, N=self.N, lambda2=self.lambda2)

        i = 0
        for result in tqdm(
            pool.imap(func=func, iterable=param_list),
            total = n_simulations
        ):
            params = param_list[i]
            if result is not None:
                filename = f"{self.outdir}/results_{i}.npz"
                np.savez(filename, **result)
                index.append({"succeded": True, "params": params, "filename": filename})
            else:
                index.append({"succeded": False, "params": params, "error":""})
            i += 1
            if (i+1) % 10 == 0:
                print(f"Completed {i+1} of {len(param_list)} simulations, saving index file.")
                with open(index_file, "w") as fp:
                    json.dump(index, fp)

        print(f"Completed all {len(param_list)} simulations, saving index file.")
        with open(index_file, "w") as fp:
            json.dump(index, fp)

if __name__ == "__main__":
    sim = BatchSimulator("test", warmup_time=0.001)
    sim.run_batch({"initial_guess": {"lambda2": 2}}, ranges={"mu_scale": [.8,1.8], "alpha_scale": [.01, .1], "dt":[1e-2, 1e-3, 1e-4]})