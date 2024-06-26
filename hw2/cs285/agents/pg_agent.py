from typing import Optional, Sequence
import numpy as np
import torch

from cs285.networks.policies import MLPPolicyPG
from cs285.networks.critics import ValueCritic
from cs285.infrastructure import pytorch_util as ptu
from torch import nn


class PGAgent(nn.Module):
    def __init__(
        self,
        ob_dim: int,
        ac_dim: int,
        discrete: bool,
        n_layers: int,
        layer_size: int,
        gamma: float,
        learning_rate: float,
        use_baseline: bool,
        use_reward_to_go: bool,
        baseline_learning_rate: Optional[float],
        baseline_gradient_steps: Optional[int],
        gae_lambda: Optional[float],
        normalize_advantages: bool,
    ):
        super().__init__()

        # create the actor (policy) network
        self.actor = MLPPolicyPG(
            ac_dim, ob_dim, discrete, n_layers, layer_size, learning_rate
        )

        # create the critic (baseline) network, if needed
        if use_baseline:
            self.critic = ValueCritic(
                ob_dim, n_layers, layer_size, baseline_learning_rate
            )
            self.baseline_gradient_steps = baseline_gradient_steps
        else:
            self.critic = None

        # other agent parameters
        self.gamma = gamma
        self.use_reward_to_go = use_reward_to_go
        self.gae_lambda = gae_lambda
        self.normalize_advantages = normalize_advantages

    def update(
        self,
        obs: Sequence[np.ndarray],
        actions: Sequence[np.ndarray],
        rewards: Sequence[np.ndarray],
        terminals: Sequence[np.ndarray],
    ) -> dict:
        """The train step for PG involves updating its actor using the given observations/actions and the calculated
        qvals/advantages that come from the seen rewards.

        Each input is a list of NumPy arrays, where each array corresponds to a single trajectory. The batch size is the
        total number of samples across all trajectories (i.e. the sum of the lengths of all the arrays).
        """

        # step 1: calculate Q values of each (s_t, a_t) point, using rewards (r_0, ..., r_t, ..., r_T)
        q_values: Sequence[np.ndarray] = self._calculate_q_vals(rewards)

        # TODO: flatten the lists of arrays into single arrays, so that the rest of the code can be written in a vectorized
        # way. obs, actions, rewards, terminals, and q_values should all be arrays with a leading dimension of `batch_size`
        # beyond this point.
        obs=np.array([item for sublist in obs for item in sublist])
        actions=np.array([item for sublist in actions for item in sublist])
        rewards=np.array([item for sublist in rewards for item in sublist])
        terminals=np.array([item for sublist in terminals for item in sublist])
        q_values=np.array([item for sublist in q_values for item in sublist])
        # step 2: calculate advantages from Q values
        advantages: np.ndarray = self._estimate_advantage(
            obs, rewards, q_values, terminals
        )

        # step 3: use all datapoints (s_t, a_t, adv_t) to update the PG actor/policy
        # TODO: update the PG actor/policy network once using the advantages
        info: dict = self.actor.update(obs=obs,actions=actions,advantages=advantages)

        # step 4: if needed, use all datapoints (s_t, a_t, q_t) to update the PG critic/baseline
        if self.critic is not None:
            # TODO: perform `self.baseline_gradient_steps` updates to the critic/baseline network
            for i in range(self.baseline_gradient_steps):
                critic_info: dict = self.critic.update(obs, q_values)
            info.update(critic_info)

        return info

    def _calculate_q_vals(self, rewards: Sequence[np.ndarray]) -> Sequence[np.ndarray]:
        """Monte Carlo estimation of the Q function."""
        q_values = []

        if not self.use_reward_to_go:
            # Case 1: Use the total discounted return from the start of the trajectory for each point
            for reward in rewards:
                total_discounted_return = self._discounted_return(reward)
                # Extend the q_values list with repeated total discounted return for the length of the trajectory
                q_values.append([total_discounted_return] * len(reward))
        else:
            # Case 2: Use the discounted reward to go for each point in the trajectory
            for reward in rewards:
                q_values.append(self._discounted_reward_to_go(reward))

        return q_values

    def _estimate_advantage(
        self,
            obs: np.ndarray,
            rewards: np.ndarray,
            q_values: np.ndarray,
            terminals: np.ndarray,
    ) -> np.ndarray:
        """Computes advantages by (possibly) subtracting a value baseline from the estimated Q-values.

        Operates on flat 1D NumPy arrays.
        """

        if self.critic is None:
            # TODO: if no baseline, then what are the advantages?
            advantages = q_values.copy()

        else: # use baseline
            # TODO: run the critic and use it as a baseline
            values = self.critic(ptu.from_numpy(obs)).squeeze()
            assert values.shape == q_values.shape

            if self.gae_lambda is None:
                # TODO: if using a baseline, but not GAE, what are the advantages?
                    advantages = q_values.copy()-ptu.to_numpy(values)
                # advantages = {reawrd to go}-value function_pi(s_it)

            else:
                # TODO: implement GAE
                batch_size = obs.shape[0]
                values_gae = values.detach().cpu().numpy()
                # HINT: append a dummy T+1 value for simpler recursive calculation
                values_gae = np.append(values_gae, [0])
                advantages = np.zeros(batch_size + 1)
                delta_T=0
                for i in reversed(range(batch_size)):
                    # TODO: recursively compute advantage estimates starting from timestep T.
                    # HINT: use terminals to handle edge cases. terminals[i] is 1 if the state is the last in its
                    # trajectory, and 0 otherwise.
                    if terminals[i]==1: # edge
                        delta_T=rewards[i]-values[i]
                        advantages[i]=delta_T
                    else:
                        delta_T = rewards[i] + self.gamma*values[i+1] - values[i]
                        advantages[i]=delta_T+self.gamma*self.gae_lambda*advantages[i+1]


                # remove dummy advantage
                advantages = advantages[:-1]

        # TODO: normalize the advantages to have a mean of zero and a standard deviation of one within the batch
        if self.normalize_advantages:

            ind=1
            adv_ind=0
            for terminal in terminals:
                if terminal == 1:
                    advantages[adv_ind:adv_ind+ind]/=ind
                    adv_ind+=ind
                    ind=1
                else:
                    ind+=1
        return np.array(advantages)

    def _discounted_return(self, rewards: Sequence[float]) -> Sequence[float]:
        """
        Helper function which takes a list of rewards {r_0, r_1, ..., r_t', ... r_T} and returns
        a list where each index t contains sum_{t'=0}^T gamma^t' r_{t'}

        Note that all entries of the output list should be the exact same because each sum is from 0 to T (and doesn't
        involve t)!
        """
        discount_rewards=list()
        for i in range(len(rewards)):
            discount_rewards.append(self.gamma ** i * rewards[i])
        _sum = sum(discount_rewards)
        new_rewards = list()
        for i in range(len(rewards)):
            new_rewards.append(_sum)
        return new_rewards

    def _discounted_reward_to_go(self, rewards: Sequence[float]) -> Sequence[float]:
        n = len(rewards)
        discount_rewards_to_go = [0] * n
        cumulative = 0
        for i in reversed(range(n)):
            cumulative = rewards[i] + self.gamma * cumulative  # 현재 보상 + (할인된 미래 보상)
            discount_rewards_to_go[i] = cumulative
        return discount_rewards_to_go
