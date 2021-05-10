import gym
from gym import spaces

import airsim
from airgym.envs.airsim_env import AirSimEnv

import numpy as np
import math
import time
from argparse import ArgumentParser


class AirSimDroneEnv(AirSimEnv):


    def __init__(self, ip_address, step_length, image_shape, destination):
        super().__init__(image_shape)
        self.step_length = step_length
        self.image_shape = image_shape
        self.destination = destination
        self.total_rewards = float(0.0)

        #NED coordinate system (X,Y,Z) : +X is North, +Y is East and +Z is Down
        self.MAX_UPWARD = -60
        self.MAX_DOWNWARD = -10
        self.MAX_WEST = -abs(destination[1]) - 100
        self.MAX_EAST = abs(destination[1]) + 100
        self.MAX_SOUTH =  -100
        self.MAX_NORTH = abs(destination[0]) + 100
        
        self.state = {
            "position": np.zeros(3),
            "collision": False,
            "prev_position": np.zeros(3),
        }

        self.drone = airsim.MultirotorClient(ip=ip_address)
        # self.action_space = spaces.Discrete(6)    
        self.action_space = spaces.Discrete(3)
        self.destination = self._setup_destination()
        self._setup_flight()

        self.image_request = airsim.ImageRequest(
            3, airsim.ImageType.DepthPerspective, True, False
        )

    def __del__(self):
        self.drone.reset()

    def _setup_flight(self):
        self.drone.reset()
        self.drone.enableApiControl(True)
        self.drone.armDisarm(True)

        # Set home position and velocity
        self.drone.moveToPositionAsync(0, 0, -40, 6).join()
        self.drone.moveByVelocityAsync(1, 0, 0, 1).join()

    def _setup_destination(self):
        #random area A,B,C (ratio 1:2:3)

        return np.array([x,y,z])

    def transform_obs(self, responses):
        img1d = np.array(responses[0].image_data_float, dtype=np.float)
        img1d = 255 / np.maximum(np.ones(img1d.size), img1d)
        img2d = np.reshape(img1d, (responses[0].height, responses[0].width))

        from PIL import Image

        image = Image.fromarray(img2d)
        im_final = np.array(image.resize((84, 84)).convert("L"))

        return im_final.reshape([84, 84, 1])

    def _get_obs(self):
        responses = self.drone.simGetImages([self.image_request])
        image = self.transform_obs(responses)
        self.drone_state = self.drone.getMultirotorState()

        self.state["prev_position"] = self.state["position"]
        self.state["position"] = self.drone_state.kinematics_estimated.position
        self.state["velocity"] = self.drone_state.kinematics_estimated.linear_velocity

        collision = self.drone.simGetCollisionInfo().has_collided
        self.state["collision"] = collision

        return image

    def _do_action(self, action):
        quad_offset = self.interpret_action(action)
        quad_vel = self.drone.getMultirotorState().kinematics_estimated.linear_velocity
        self.drone.moveByVelocityAsync(
            quad_vel.x_val + quad_offset[0],
            quad_vel.y_val + quad_offset[1],
            quad_vel.z_val + quad_offset[2],
            5,
        ).join()
    

    def _compute_reward(self, action):
        rewards = float(0.0)
        reward_dist = 0
        reward_speed = 0

        quad_pt = np.array(list((self.state["position"].x_val,
                                 self.state["position"].y_val,
                                 self.state["position"].z_val,)))

        prev_quad_pt = np.array(list((self.state["prev_position"].x_val,
                                      self.state["prev_position"].y_val,
                                      self.state["prev_position"].z_val,)))

        distance = 0.0
        done = False

        if self.state["collision"]:
            rewards = -100
            done = True
            print("done : collision")
        elif self.state["position"].z_val < self.MAX_UPWARD or\
            self.state["position"].z_val > self.MAX_DOWNWARD or\
            self.state["position"].y_val < self.MAX_WEST or\
            self.state["position"].y_val > self.MAX_EAST or\
            self.state["position"].x_val < self.MAX_SOUTH or\
            self.state["position"].x_val > self.MAX_NORTH:
                reward = -100
                done = True
                print("done : out of range")
        else:
            distance = np.linalg.norm(self.destination - quad_pt)
            prev_distance = np.linalg.norm(self.destination - prev_quad_pt) 

            if distance > prev_distance:
                rewards = -2
            elif distance == prev_distance:
                rewards = 0
            else:
                if distance == 0:
                    reward_dist = 100
                    done = True
                    print("done : arrive at destination")
                else:
                    reward_dist = 10/distance

                reward_speed = np.linalg.norm([self.state["velocity"].x_val,
                                            self.state["velocity"].y_val,
                                            self.state["velocity"].z_val,])
                rewards = reward_dist + reward_speed
    

        self.total_rewards += rewards
        if self.total_rewards < -150:
            done = True

        # print("reward ", format(reward, ".3f") , "\t[  " , format(reward_dist, ".3f"), ", ", format(reward_speed, ".3f"), " ]\ttotal ", format(self.total_rewards, ".3f"), "\tdistance ", format(distance, ".2f") )

        return rewards, done


    def step(self, action):
        self._do_action(action)
        obs = self._get_obs()
        reward, done = self._compute_reward(action)

        print("reward ", format(reward, ".2f"), "\ttotal_reward ", format(self.total_rewards, ".2f") , "\tdone " + str(done) )

        if done:
            self.total_rewards = 0

        return obs, reward, done, self.state

    def reset(self):
        self.destination = self._setup_destination()
        self._setup_flight()
        return self._get_obs()

    def interpret_action(self, action):
        #NED coordinate system (X,Y,Z) : +X is North, +Y is East and +Z is Down
        if action == 0: # forward
            quad_offset = (self.step_length, 0, 0)
        elif action == 1: # slide right
            quad_offset = (0, self.step_length, 0)
        # elif action == 2: # downward
        #     quad_offset = (0, 0, self.step_length)
        # # elif action == 3: # backward
        #     quad_offset = (-self.step_length, 0, 0)
        elif action == 2: # slide left
            quad_offset = (0, -self.step_length, 0)
        # elif action == 5: # upward
        #     quad_offset = (0, 0, -self.step_length)
        # else: # Origin
        #     quad_offset = (0, 0, 0)

        return quad_offset
