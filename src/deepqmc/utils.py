import time
from collections import UserDict
from contextlib import contextmanager
from datetime import datetime
from functools import partial, wraps

import numpy as np
import torch


def get_flat_mesh(bounds, npts, device=None):
    edges = [torch.linspace(*b, n, device=device) for b, n in zip(bounds, npts)]
    grids = torch.meshgrid(*edges)
    return torch.stack(grids).flatten(start_dim=1).t(), edges


def plot_func(
    func,
    bounds,
    density=0.02,
    x_line=False,
    is_torch=True,
    device=None,
    double=False,
    ax=None,
    **kwargs,
):
    if not ax:
        import matplotlib.pyplot as plt

        ax = plt.gca()
    n_pts = int((bounds[1] - bounds[0]) / density)
    x = torch.linspace(bounds[0], bounds[1], n_pts)
    if x_line:
        x = torch.cat([x[:, None], x.new_zeros((n_pts, 2))], dim=1)
    if not is_torch:
        x = x.numpy()
    else:
        if device:
            x = x.to(device)
        if double:
            x = x.double()
    y = func(x)
    if is_torch:
        x = x.cpu().numpy()
        y = y.detach().cpu().numpy()
    if x_line:
        x = x[:, 0]
    return ax.plot(x, y, **kwargs)


def plot_func_2d(func, bounds, density=0.02, xy_plane=False, device=None, ax=None):
    if not ax:
        import matplotlib.pyplot as plt

        ax = plt.gca()
    ns_pts = [int((bs[1] - bs[0]) / density) for bs in bounds]
    xy, x_y = get_flat_mesh(bounds, ns_pts, device=device)
    if xy_plane:
        xy = torch.cat([xy, xy.new_zeros(len(xy), 1)], dim=1)
    res = plt.contour(
        *(z.cpu().numpy() for z in x_y),
        func(xy).detach().view(len(x_y[0]), -1).cpu().numpy().T,
    )
    plt.gca().set_aspect(1)
    return res


plot_func_x = partial(plot_func, x_line=True)
plot_func_xy = partial(plot_func_2d, xy_plane=True)


def integrate_on_mesh(func, bounds, density=0.02):
    ns_pts = [int((bs[1] - bs[0]) / density) for bs in bounds]
    vol = np.array([bs[1] - bs[0] for bs in bounds]).prod()
    mesh = get_flat_mesh(bounds, ns_pts)[0]
    return sum(func(x).sum() for x in mesh.chunk(100)) * (vol / mesh.shape[0])


def assign_where(xs, ys, where):
    assert len(xs) == len(ys)
    for x, y in zip(xs, ys):
        x[where] = y[where]


class InfoException(Exception):
    def __init__(self, info=None):
        self.info = info or {}
        super().__init__(self.info)


class DebugContainer(UserDict):
    def __init__(self):
        super().__init__()
        self._levels = []

    @contextmanager
    def cd(self, label):
        self._levels.append(label)
        try:
            yield
        finally:
            assert label == self._levels.pop()

    def _getkey(self, key):
        if isinstance(key, int) and not self._levels:
            return key
        return '.'.join([*self._levels, str(key)])

    def __getitem__(self, key):
        key = self._getkey(key)
        try:
            val = super().__getitem__(key)
        except KeyError:
            val = self.__class__()
            self.__setitem__(key, val)
        return val

    def __setitem__(self, key, val):
        if isinstance(val, torch.Tensor):
            val = val.detach().cpu()
        super().__setitem__(self._getkey(key), val)

    def result(self, val):
        super().__setitem__('.'.join(self._levels), val)
        return val


class _NullDebug(DebugContainer):
    def __setitem__(self, key, val):
        pass


NULL_DEBUG = _NullDebug()


def debugged(func, label):
    @wraps(func)
    def wrapped(*args, **kwargs):
        debug = DebugContainer()
        func(*args, **kwargs, debug=debug)
        return debug[label]

    return wrapped


class Debuggable:
    def debug(self, label):
        return debugged(self, label)


def batch_eval(func, batches, *args, **kwargs):
    return torch.cat([func(batch, *args, **kwargs) for batch in batches])


def batch_eval_tuple(func, batches, *args, **kwargs):
    results = list(zip(*(func(batch, *args, **kwargs) for batch in batches)))
    return tuple(torch.cat(result) for result in results)


def number_of_parameters(net):
    return sum(p.numel() for p in net.parameters())


def state_dict_copy(net):
    return {name: val.cpu() for name, val in net.state_dict().items()}


def shuffle_tensor(x):
    return x[torch.randperm(len(x))]


def pow_int(xs, exps):
    batch_dims = xs.shape[: -len(exps.shape)]
    zs = xs.new_zeros(*batch_dims, *exps.shape)
    xs_expanded = xs.expand_as(zs)
    for exp in exps.unique():
        mask = exps == exp
        zs[..., mask] = xs_expanded[..., mask] ** exp.item()
    return zs


@contextmanager
def timer():
    now = np.array(time.time())
    try:
        yield now
    finally:
        now[...] = time.time() - now


def now():
    return datetime.now().isoformat(timespec='seconds')


def expand_1d(r, x, k, i):
    rs = r.repeat(len(x), 1, 1)
    rs[:, k, i] += x
    return rs


def normalize_mean(x):
    return x / x.mean()


def weighted_mean_var(xs, ws):
    ws = normalize_mean(ws)
    mean = (ws * xs).mean()
    return mean, (ws * (xs - mean) ** 2).mean()


class NestedDict(dict):
    def __init__(self, dct=None):
        super().__init__()
        if dct:
            self.update(dct)

    def _split_key(self, key):
        key, *nested_key = key.split('.', 1)
        return (key, nested_key[0]) if nested_key else (key, None)

    def __getitem__(self, key):
        key, nested_key = self._split_key(key)
        try:
            val = super().__getitem__(key)
        except KeyError:
            val = NestedDict()
            super().__setitem__(key, val)
        if nested_key:
            return val[nested_key]
        return val

    def __setitem__(self, key, val):
        key, nested_key = self._split_key(key)
        if nested_key:
            self[key][nested_key] = val
        else:
            super().__setitem__(key, val)

    def __delitem__(self, key):
        key, nested_key = self._split_key(key)
        if nested_key:
            del super().__getitem__(key)[nested_key]
        else:
            super().__delitem__(key)

    def update(self, other):
        for key, val in other.items():
            if isinstance(val, dict):
                if not isinstance(self[key], NestedDict):
                    if isinstance(self[key], dict):
                        self[key] = NestedDict(self[key])
                    else:
                        self[key] = NestedDict()
                super().__getitem__(key).update(val)
            else:
                super().__setitem__(key, val)