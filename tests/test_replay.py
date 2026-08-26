"""Prioritized replay + NumPy Q-network tests."""
import numpy as np

from atsc.agents.net import NumpyQNet
from atsc.agents.replay import PrioritizedReplayBuffer, SumTree, UniformReplayBuffer


def test_sumtree_total_and_sampling():
    tree = SumTree(8)
    for i in range(8):
        tree.add(float(i + 1), i)
    assert abs(tree.total - sum(range(1, 9))) < 1e-9
    # sampling near the top cumulative value returns a high-priority leaf
    _, prio, idx = tree.get_leaf(tree.total - 0.01)
    assert 0 <= idx < 8


def test_per_add_sample_update():
    buf = PrioritizedReplayBuffer(capacity=64, obs_dim=4, beta_steps=100)
    for i in range(64):
        buf.add(np.ones(4) * i, i % 2, float(-i), np.zeros(4), i == 63)
    batch = buf.sample(16)
    assert batch.states.shape == (16, 4)
    assert batch.weights.shape == (16,)
    assert (batch.weights <= 1.0 + 1e-6).all()
    # updating priorities with large TD errors should not raise
    buf.update_priorities(batch.indices, np.abs(np.random.randn(16)) * 5)


def test_uniform_buffer_roundtrip():
    buf = UniformReplayBuffer(32, 3)
    for i in range(40):  # wraps around
        buf.add(np.ones(3) * i, 0, 1.0, np.ones(3), False)
    assert len(buf) == 32
    b = buf.sample(8)
    assert b.states.shape == (8, 3)


def test_numpy_qnet_learns_fixed_target():
    net = NumpyQNet(obs_dim=5, n_actions=3, hidden_sizes=[24, 24], lr=5e-3, seed=0)
    rng = np.random.default_rng(0)
    X = rng.standard_normal((96, 5))
    a = rng.integers(0, 3, 96)
    y = (X[:, 0] * 1.5 - X[:, 2]).astype(float)
    w = np.ones(96)
    first = net.train_batch(X, a, y, w)[0]
    for _ in range(400):
        net.train_batch(X, a, y, w)
    last = net.train_batch(X, a, y, w)[0]
    assert last < first * 0.3, "NumPy Q-net failed to reduce loss"


def test_numpy_qnet_weight_portability():
    net = NumpyQNet(4, 2, [16], seed=1)
    X = np.random.randn(10, 4)
    w = net.get_weights()
    clone = NumpyQNet(4, 2, [16], seed=99)
    clone.set_weights(w)
    assert np.allclose(net.q(X), clone.q(X))
