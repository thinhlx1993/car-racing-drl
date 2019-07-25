""" 
Implementation of DDPG - Deep Deterministic Policy Gradient

Algorithm and hyperparameter details can be found here: 
    http://arxiv.org/pdf/1509.02971v2.pdf

The algorithm is tested on the Pendulum-v0 OpenAI gym task 
and developed with tflearn + Tensorflow

Author: Patrick Emami
"""
import tensorflow as tf
import numpy as np
import gym 
import tflearn
import matplotlib.pyplot as plt

from replay_buffer_ddpg import ReplayBuffer
from skimage.color import rgb2grey

# ==========================
#   Training Parameters
# ==========================
# Max training steps
MAX_EPISODES = 500000
# Max episode length
MAX_EP_STEPS = 500
# Base learning rate for the Actor network
INITIAL_LR = 0.0001
MINI_LR = 1e-6
# Base learning rate for the Critic Network
# CRITIC_INITIAL_LR = 0.0001
# Discount factor 
GAMMA = 0.9
# Soft target update param
TAU = 0.01
EPS_DECAY_RATE = 0.99
LR_DECAY_RATE = 0.99
# ===========================
#   Utility Parameters
# ===========================
# Render gym env during training
RENDER_ENV = True
# Use Gym Monitor
GYM_MONITOR_EN = False
# Gym environment
ENV_NAME = 'CarRacing-v0'
# Directory for storing gym results
MONITOR_DIR = './results/gym_ddpg'
# Directory for storing tensorboard summary results
SUMMARY_DIR = './results/tf_ddpg'
RANDOM_SEED = 1234
# Size of replay buffer
BUFFER_SIZE = 10000
MINIBATCH_SIZE = 64

SAVE_STEP = 200


# ===========================
#   Actor and Critic DNNs
# ===========================
class ActorNetwork(object):
    """ 
    Input to the network is the state, output is the action
    under a deterministic policy.

    The output layer activation is a tanh to keep the action
    between -2 and 2
    """
    def __init__(self, sess, state_dim, action_dim, action_bound, tau):
        self.sess = sess
        self.s_dim = state_dim
        self.a_dim = action_dim
        self.action_bound = action_bound
        self.tau = tau

        # Actor Network
        self.inputs, self.scaled_out = self.create_actor_network()

        self.learning_rate = tf.placeholder(tf.float32, [None,])

        self.network_params = tf.trainable_variables()

        # Target Network
        self.target_inputs, self.target_scaled_out = self.create_actor_network()
        
        self.target_network_params = tf.trainable_variables()[len(self.network_params):]

        # Op for periodically updating target network with online network weights
        self.update_target_network_params = \
            [self.target_network_params[i].assign(tf.multiply(self.network_params[i], self.tau) + \
                tf.multiply(self.target_network_params[i], 1. - self.tau))
                for i in range(len(self.target_network_params))]

        # This gradient will be provided by the critic network
        self.action_gradient = tf.placeholder(tf.float32, [None, self.a_dim])
        
        # Combine the gradients here 
        self.actor_gradients = tf.gradients(self.scaled_out, self.network_params, -self.action_gradient)

        # Optimization Op
        self.lr = tf.gather_nd(self.learning_rate,[0])
        self.optimize = tf.train.AdamOptimizer(self.lr).\
            apply_gradients(zip(self.actor_gradients, self.network_params))

        self.num_trainable_vars = len(self.network_params) + len(self.target_network_params)

    def create_actor_network(self): 
        inputs = tflearn.input_data(shape=[None, self.s_dim[0], self.s_dim[1], self.s_dim[2]])
        net = tflearn.conv_2d(inputs, 8, 8, activation='relu', name='actor_conv1')
        net = tflearn.conv_2d(inputs, 16, 8, activation='relu', name='actor_conv2')
        net = tflearn.layers.normalization.batch_normalization (net, name='actor_BatchNormalization1')
        net = tflearn.fully_connected(inputs, 50, activation='relu')
        # net = tflearn.layers.normalization.batch_normalization (net, name='actor_BatchNormalization1')
        # net = tflearn.fully_connected(net, 50, activation='relu')
        net = tflearn.layers.normalization.batch_normalization (net, name='actor_BatchNormalization2')
        # Final layer weights are init to Uniform[-3e-3, 3e-3]
        w_init = tflearn.initializations.uniform(minval=-0.003, maxval=0.003)
        steering = tflearn.fully_connected(net, 1, activation='tanh', weights_init=w_init)
        acceleration = tflearn.fully_connected(net, 1, activation='sigmoid', weights_init=w_init)
        brake = tflearn.fully_connected(net, 1, activation='sigmoid', weights_init=w_init)
        scaled_out = tf.concat([steering,acceleration,brake],1)

        return inputs, scaled_out 

    def train(self, inputs, a_gradient, lr):
        self.sess.run(self.optimize, feed_dict={
            self.inputs: inputs,
            self.action_gradient: a_gradient,
            self.learning_rate: lr
        })

    def predict(self, inputs):
        return self.sess.run(self.scaled_out, feed_dict={
            self.inputs: inputs
        })

    def predict_target(self, inputs):
        return self.sess.run(self.target_scaled_out, feed_dict={
            self.target_inputs: inputs
        })

    def update_target_network(self):
        self.sess.run(self.update_target_network_params)

    def get_num_trainable_vars(self):
        return self.num_trainable_vars


class CriticNetwork(object):
    """ 
    Input to the network is the state and action, output is Q(s,a).
    The action must be obtained from the output of the Actor network.

    """
    def __init__(self, sess, state_dim, action_dim, tau, num_actor_vars):
        self.sess = sess
        self.s_dim = state_dim
        self.a_dim = action_dim
        self.tau = tau

        # Create the critic network
        self.inputs, self.action, self.out = self.create_critic_network()

        self.network_params = tf.trainable_variables()[num_actor_vars:]
        self.learning_rate = tf.placeholder(tf.float32, [None,])

        # Target Network
        self.target_inputs, self.target_action, self.target_out = self.create_critic_network()
        
        self.target_network_params = tf.trainable_variables()[(len(self.network_params) + num_actor_vars):]

        # Op for periodically updating target network with online network weights with regularization
        self.update_target_network_params = \
            [self.target_network_params[i].assign(tf.multiply(self.network_params[i], self.tau) + tf.multiply(self.target_network_params[i], 1. - self.tau))
                for i in range(len(self.target_network_params))]
    
        # Network target (y_i)
        self.predicted_q_value = tf.placeholder(tf.float32, [None, 1])

        # Define loss and optimization Op
        self.loss = tflearn.mean_square(self.predicted_q_value, self.out)
        self.lr = tf.gather_nd(self.learning_rate,[0])
        self.optimize = tf.train.AdamOptimizer(self.lr).minimize(self.loss)

        # Get the gradient of the net w.r.t. the action
        self.action_grads = tf.gradients(self.out, self.action)

    def create_critic_network(self):
        inputs = tflearn.input_data(shape=[None, self.s_dim[0], self.s_dim[1], self.s_dim[2]])
        action = tflearn.input_data(shape=[None, self.a_dim])
        net = tflearn.conv_2d(inputs, 8, 8, activation='relu', name='critic_conv1')
        # net = tflearn.conv_2d(net, 8, 8, activation='relu', name='critic_conv2')
        net = tflearn.layers.normalization.batch_normalization (net, name='critic_BatchNormalization1')
        net = tflearn.fully_connected(net, 100, activation='relu')
        # net = tflearn.layers.normalization.batch_normalization (net, name='critic_BatchNormalization1')
        # Add the action tensor in the 2nd hidden layer
        # Use two temp layers to get the corresponding weights and biases
        t1 = tflearn.fully_connected(net, 50)
        t2 = tflearn.fully_connected(action, 50)

        net = tflearn.activation(tf.matmul(net,t1.W) + tf.matmul(action, t2.W) + t2.b, activation='relu')
        net = tflearn.layers.normalization.batch_normalization (net, name='critic_BatchNormalization2')
        # linear layer connected to 1 output representing Q(s,a) 
        # Weights are init to Uniform[-3e-3, 3e-3]
        w_init = tflearn.initializations.uniform(minval=-0.003, maxval=0.003)
        out = tflearn.fully_connected(net, 1, weights_init=w_init)
        return inputs, action, out

    def train(self, inputs, action, predicted_q_value, lr):
        return self.sess.run([self.out, self.optimize], feed_dict={
            self.inputs: inputs,
            self.action: action,
            self.predicted_q_value: predicted_q_value,
            self.learning_rate: lr
        })

    def predict(self, inputs, action):
        return self.sess.run(self.out, feed_dict={
            self.inputs: inputs,
            self.action: action
        })

    def predict_target(self, inputs, action):
        return self.sess.run(self.target_out, feed_dict={
            self.target_inputs: inputs,
            self.target_action: action
        })

    def action_gradients(self, inputs, actions): 
        return self.sess.run(self.action_grads, feed_dict={
            self.inputs: inputs,
            self.action: actions
        })

    def update_target_network(self):
        self.sess.run(self.update_target_network_params)


# ===========================
#   Tensorflow Summary Ops
# ===========================
def build_summaries(): 
    episode_reward = tf.Variable(0.)
    tf.summary.scalar("Reward", episode_reward)
    episode_ave_max_q = tf.Variable(0.)
    tf.summary.scalar("Qmax Value", episode_ave_max_q)

    summary_vars = [episode_reward, episode_ave_max_q]
    summary_ops = tf.summary.merge_all()

    return summary_ops, summary_vars


# ===========================
#   Agent Training
# ===========================
def train(sess, env, actor, critic, global_step):

    # Set up summary Ops
    summary_ops, summary_vars = build_summaries()

    sess.run(tf.global_variables_initializer())

    # load model if have
    saver = tf.train.Saver()
    checkpoint = tf.train.get_checkpoint_state("./results")
    
    if checkpoint and checkpoint.model_checkpoint_path:
        saver.restore(sess, checkpoint.model_checkpoint_path)
        print("Successfully loaded:", checkpoint.model_checkpoint_path)
        print("global step: ", global_step.eval())

    else:
        print("Could not find old network weights")

    writer = tf.summary.FileWriter(SUMMARY_DIR, sess.graph)

    # Initialize target network weights
    actor.update_target_network()
    critic.update_target_network()

    # Initialize replay memory
    replay_buffer = ReplayBuffer(BUFFER_SIZE, RANDOM_SEED)

    i = global_step.eval()
    eps = 0.99
    lr = INITIAL_LR

    while True:
        i += 1
        s = env.reset()
        # s = prepro(s)
        ep_reward = 0
        ep_ave_max_q = 0
        eps *= EPS_DECAY_RATE
        lr *= LR_DECAY_RATE
        lr = np.max([lr, MINI_LR]) # minimum of learning rate is MINI_LR
        if i % SAVE_STEP == 0 : # save check point every 1000 episode
            sess.run(global_step.assign(i))
            save_path = saver.save(sess, "./results/model.ckpt" , global_step = global_step)
            print("Model saved in file: %s" % save_path)
            print("Successfully saved global step: ", global_step.eval())

        for j in range(MAX_EP_STEPS):

            if RENDER_ENV: 
                env.render()
            # print(s.shape)

            if np.random.rand() <= eps:
                action = np.random.uniform(-1, 1, 3)
                action[1] = action[1] + np.random.uniform(0, 1)
                action = np.expand_dims(action, axis=0)
            else:
                action = actor.predict(np.reshape(s,(-1,96,96,3)))
            # np.random.uniform(0, 1, 3)
            # action = a[0] + 1./(1+i+j) # add noise for exploration
            noise = np.random.normal(0,0.2*eps, 3)
            noise[1] = np.random.normal(0.4,0.1*eps)
            action[0] = action[0] + noise
            s2, r, terminal, info = env.step(action[0])
            # s2 = prepro(s2)
            # plt.imshow(s2)
            # plt.show()
            # if r > 0:
            #     r = 1
            # elif r < 0:
            #     r = -1
            # print 'r: ',r
            # replay_buffer.add(np.reshape(s, (96, 96, 3)), np.reshape(action, (actor.a_dim,)), r,
            #     terminal, np.reshape(s2, (96, 96, 3)),lr)
            replay_buffer.add(s, np.reshape(action, (actor.a_dim,)), r,
                terminal, s2,lr)
            # Keep adding experience to the memory until
            # there are at least minibatch size samples
            if replay_buffer.size() > MINIBATCH_SIZE:     
                s_batch, a_batch, r_batch, t_batch, s2_batch, lr_batch = \
                    replay_buffer.sample_batch(MINIBATCH_SIZE)

                # Calculate targets
                target_q = critic.predict_target(s2_batch, actor.predict_target(s2_batch))

                y_i = []
                for k in range(MINIBATCH_SIZE):
                    if t_batch[k]:
                        y_i.append(r_batch[k])
                    else:
                        y_i.append(r_batch[k] + GAMMA * target_q[k])

                # Update the critic given the targets
                predicted_q_value, _ = critic.train(s_batch, a_batch, np.reshape(y_i, (MINIBATCH_SIZE, 1)), lr_batch)

                ep_ave_max_q += np.amax(predicted_q_value)
                # print ep_ave_max_q
                # Update the actor policy using the sampled gradient
                a_outs = actor.predict(s_batch)
                grads = critic.action_gradients(s_batch, a_outs)
                # print grads[0]
                actor.train(s_batch, grads[0], lr_batch)

                # Update target networks
                actor.update_target_network()
                critic.update_target_network()
                summary_str = sess.run(summary_ops, feed_dict={
                    summary_vars[0]: ep_reward,
                    summary_vars[1]: ep_ave_max_q / float(j)
                })

                writer.add_summary(summary_str, i)
                writer.flush()

                print('| Reward: %.2i' % int(ep_reward), " | Episode", i, '| Qmax: %.4f' % (ep_ave_max_q / float(j)))

            s = s2
            ep_reward += r

            if terminal:

                # summary_str = sess.run(summary_ops, feed_dict={
                #     summary_vars[0]: ep_reward,
                #     summary_vars[1]: ep_ave_max_q / float(j)
                # })

                # writer.add_summary(summary_str, i)
                # writer.flush()

                # print '| Reward: %.2i' % int(ep_reward), " | Episode", i, \
                #     '| Qmax: %.4f' % (ep_ave_max_q / float(j))

                break


def main(_):
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True

    with tf.Session() as sess:
        
        global_step = tf.Variable(0, name='global_step', trainable=False)
        env = gym.make(ENV_NAME)
        np.random.seed(RANDOM_SEED)
        tf.set_random_seed(RANDOM_SEED)
        env.seed(RANDOM_SEED)

        state_dim = [96, 96, 3]
        action_dim = env.action_space.shape[0]
        action_bound = env.action_space.high
        print('state_dim: ',state_dim)
        print('action_dim: ',action_dim)
        print('action_bound: ',action_bound)
        # Ensure action bound is symmetric
        # assert (env.action_space.high == -env.action_space.low)

        actor = ActorNetwork(sess, state_dim, action_dim, action_bound, TAU)

        critic = CriticNetwork(sess, state_dim, action_dim, TAU, actor.get_num_trainable_vars())

        if GYM_MONITOR_EN:
            if not RENDER_ENV:
                env.monitor.start(MONITOR_DIR, video_callable=False, force=True)
            else:
                env.monitor.start(MONITOR_DIR, force=True)

        train(sess, env, actor, critic, global_step)

        if GYM_MONITOR_EN:
            env.monitor.close()


if __name__ == '__main__':
    tf.app.run()
