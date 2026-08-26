"""Training: curriculum loop, learner updates, checkpointing."""
from atsc.train.curriculum import Curriculum
from atsc.train.trainer import Trainer, train_main

__all__ = ["Trainer", "train_main", "Curriculum"]
