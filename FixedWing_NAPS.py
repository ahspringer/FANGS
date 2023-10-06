#!/usr/bin/env python
""" Fixed Wing (N)onlinear (A)ircraft - (P)erformance (S)imulation
        The algorithms followed for the nonlinear controller are described in the case study for a
        Nonlinear Aircraft-Performance Simulation by Dr. John Schierman in his Modern Flight Dynamics textbook.
        This project is a nonlinear controller for a fixed-wing aircraft.
        This code will allow one to model a rigid aircraft operating in steady winds.
        The aircraft will be guided via nonlinear feedback laws to follow a specified flight profile:
            - Commanded velocities
            - Commanded rates of climb/descent
            - Commanded headings
        In the current implementation, the attitude dynamics of the vehicle are approximated using ideal equations of motion.
"""
__author__ = "Alex Springer"
__version__ = "1.0.1"
__email__ = "springer.alex.h@gmail.com"
__status__ = "Production"

import numpy as np
from scipy.integrate import solve_ivp
import utils
from vehicle.FixedWingVehicle import FixedWingVehicle


class FW_NL_GuidanceSystem:
    """ Fixed-Wing Nonlinear Guidance System

    (!!) FUTURE VERSIONS WILL NOT APPROXIMATE AIRCRAFT RESPONSE.
    USER WILL HAVE TO RUN GUIDANCE SYSTEM THROUGH
    ANY EQUATIONS OF MOTION DEEMED APPROPRIATE.
    THE IDEAL EOM FROM THIS OBJECT WILL BE BROKEN OUT INTO ITS OWN CLASS.
    (!!)

    Note that fuel rate (FixedWingVehicle) mdot is not currently used.

    The FW_NL_GuidanceSystem algorithm involves the following steps:
    1. Guidance System - Generates guidance commands for the aircraft
        a. Thrust Guidance System
        b. Lift Guidance System
        c. Heading Guidance System
    2. Equations of Motion (EOM) System - Approximates aircraft responses to guidance system
        a. Aerodynamic Response
        b. Fuel Burn Response
        c. Motion Response
        d. Airspeed Response
        e. Conversion to Earth-Centered, Earth-Fixed (ECEF) Response

    Guidance System inputs:
        m           mass of the aircraft
        v_BN_W_c    commanded inertial velocity
        v_BN_W      current inertial velocity (output from EOM)
        gamma_c     commanded flight path angle
        gamma       current flight path angle (output from EOM)
        airspeed    current airspeed (output from EOM)
        sigma_c     commanded heading angle clockwise from North
        sigma       current heading angle clockwise from North (output from EOM)

    Guidance System outputs:
        thrust      magnitude of thrust vector in line with aircraft body
        lift        magnitude of lift vector in line with aircraft z-axis
        alpha_c     angle of attack commanded by guidance system (unused in EOM)
        mu          wind-axes bank angle (phi_w in textbook)
        h_c         commanded height of aircraft (? - unused in EOM)

    EOM inputs:
        thrust      thrust command
        lift        lift command
        mu          wind-axes bank angle command

    EOM outputs:
        m           mass following fuel burn
        v_BN_W      inertial velocity
        gamma       flight path angle
        sigma       heading angle clockwise from North
        lat         latitude
        lon         longitude
        h           altitude
        airspeed    airspeed
        alpha       angle of attack response
        drag        drag force

    Guidance System assumptions:
        a. Air mass (wind) uniformly translating w.r.t. Earth-fixed inertial frame
        b. Aero forces/moments on vehicle depend only on airspeed and orientation to air mass
        c. Presence of winds give rise to differences in inertial velocity and airspeed
    """

    def __init__(self, vehicle, TF_constants, InitialConditions, time = 0, dt=0.01):
        """ Initialize a fixed-wing nonlinear performance guidance system.
        
        Parameters
        ----------
        vehicle : object of class FixedWingVehicle to be commanded
            Must have the following parameters set:
                weight_max, weight_min, speed_max, speed_min, Kf, omega_T,
                omega_L, omega_mu, T_max, K_Lmax, mu_max, C_Do, C_Lalpha,
                alpha_o, wing_area, aspect_ratio, wing_eff
        TF_constants : :dict:`Dictionary of PI Guidance transfer function coefficients`
            Required keys: K_Tp, K_Ti, K_Lp, K_Li, K_mu_p
        InitialConditions : :dict:`Dictionary of Initial Conditions`
            Required keys: v_BN_W, h, gamma, sigma, lat, lon, v_WN_N, weight
        time : :float:`Time of vehicle GNC initialization`
            Default value is 0. This can be used for vehicles spawned at varying times.
        dt : :float:`Time delta to be used for integration and next step calculations`
            Can also be specified at any later time for non-uniform time steps
        """
        self.Vehicle = vehicle

        # Set tuning parameters
        self.K_Tp = TF_constants['K_Tp']
        self.K_Ti = TF_constants['K_Ti']
        self.K_Lp = TF_constants['K_Lp']
        self.K_Li = TF_constants['K_Li']
        self.K_mu_p = TF_constants['K_mu_p']
        self.dt = dt  # Default is 0.01 seconds

        # Set Initial Conditions
        self.time = [time]
        self.v_BN_W = [InitialConditions['v_BN_W']]
        self.h = [InitialConditions['h']]
        self.gamma = [InitialConditions['gamma']]
        self.sigma = [InitialConditions['sigma']]
        self.lat = [InitialConditions['lat']]
        self.lon = [InitialConditions['lon']]
        self.v_WN_N = [InitialConditions['v_WN_N']]
        self.weight = [InitialConditions['weight']]
        self.mass = [self.weight[0]/utils.const_gravity]
        self.airspeed = [utils.wind_vector(self.v_BN_W[0], self.gamma[0], self.sigma[0])]

        # Set vehicle GNC initiation time
        self.time = [time]

        # Initialize user commands and internal variables
        self.command = self.userCommand(self.v_BN_W[0], self.gamma[0], self.sigma[0])
        self.v_BN_W_c_hist = [0]
        self.gamma_c_hist = [0]
        self.sigma_c_hist = [0]
        self.units = self.Vehicle.units  # Adopt units from vehicle at init
        self.angles = self.Vehicle.angles  # Adopt angle units from vehicle at init
        self.V_err = 0
        self.xT = 0
        self.hdot_err = 0
        self.Tc = 0
        self.Thrust = [0]
        self.xL = 0
        self.Lc = 0
        self.Lift = [0]
        self.alpha_c = [0]
        self.h_c = [0]
        self.sigma_err = 0

        # Calculate initial alpha, drag, and mu
        self.alpha = [self._calculateAlpha()]
        self.drag = [self._calculateDrag()]
        self.mu = [self._calculateMu()]

    class userCommand:
        def __init__(self, v_BN_W, gamma, sigma):
            self.v_BN_W = v_BN_W
            self.gamma = gamma
            self.sigma = sigma
            self.v_BN_W_history = [v_BN_W]
            self.gamma_history = [gamma]
            self.sigma_history = [sigma]
            self.airspeed = utils.wind_vector(v_BN_W, gamma, sigma)
            self.airspeed_history = [self.airspeed]
        
        def save_history(self):
            self.v_BN_W_history.append(self.v_BN_W)
            self.gamma_history.append(self.gamma)
            self.sigma_history.append(self.sigma)
            self.airspeed_history.append(self.airspeed)

    def setCommandTrajectory(self, velocity, flight_path_angle, heading):
        """ Set a user-defined commanded aircraft trajectory
        
        The trajectory set using this command will come into effect on the next iteration of the guidance system.

        Parameters
        ----------
        velocity : :float:`(feet per second) The commanded forward velocity of the aircraft.`
            Use this command to set the forward airspeed of the aircraft.
        flight_path_angle : :float:`(radians) The commanded flight path angle of the aircraft.`
            The flight path angle is the angle at which the aircraft is either climbing (+) or descending (-)
        heading : :float:`(radians) The commanded heading of the aircraft.`
            The heading of the aircraft is defined as clockwise from North.
        """
        # Set the new user command
        self.command.v_BN_W = velocity  # Commanded velocity
        self.command.gamma = flight_path_angle  # Commanded flight path angle
        self.command.sigma = heading  # Commanded heading
        self.command.airspeed = utils.wind_vector(self.command.v_BN_W, self.command.gamma, self.command.sigma)

        # Update errors
        self.V_err = self.command.v_BN_W - self.v_BN_W[-1]  # Calculate inertial velocity error
        self.hdot_err = self.command.v_BN_W*(np.sin(self.command.gamma) - np.sin(self.gamma[-1]))
        self.sigma_err = self.command.sigma - self.sigma[-1]

    def stepTime(self, dt=None):
        """ Proceed one time step forward in the simulation.
        
        Parameters
        ----------
        dt : :float:`Optional. Time step value.
        """
        if dt is None:
            dt = self.dt
        self.getGuidanceCommands(dt)
        self.getEquationsOfMotion_Ideal(dt)
        self.command.save_history()
        self.time.append(self.time[-1]+dt)

    def getGuidanceCommands(self, dt=None):
        """ Get the Guidance System outputs based on current state and commanded trajectory.
        Note: Be sure to check the current vehicle units via:
            > [FW_NL_GuidanceSystem].Vehicle.units
            > [FW_NL_GuidanceSystem].Vehicle.angles
            **At the initialization of the guidance system, the units of the vehicle were inherited.
                However, it is recommended to check the current guidance system units as well:
                > [FW_NL_GuidanceSystem].units
                > [FW_NL_GuidanceSystem].angles

        Parameters
        ----------
        dt : :float:`Optional. Time step value.
        """
        if np.nan in [self.command.v_BN_W, self.command.gamma, self.command.sigma]:
            print('Unable to get Guidance commands because no User Trajectory Command has been set.')
            return

        if dt is None:
            dt = self.dt

        self._thrustGuidanceSystem(dt)
        self._liftGuidanceSystem(dt)
        self._headingGuidanceSystem(dt)

    def getEquationsOfMotion_Ideal(self, dt=None):
        """ An ideal equations of motion solver for a rigid body fixed-wing aircraft.
        In future versions, this will be removed and the user will solve the EOM. The user
        will then have to provide the responses of the aircraft back to the guidance system
        after each time step.

        Parameters
        ----------
        dt : :float:`Optional. Time step value.
        """
        if dt is None:
            dt = self.dt

        # Calculate fuel burn based on thrust
        sol = solve_ivp(self.__m_dot_ode, [self.time[-1], self.time[-1] + dt], [self.mass[-1]], method='RK45')
        self.mass.append(sol.y[-1][-1])

        # Calculate alpha and drag
        a = self._calculateAlpha()
        d = self._calculateDrag()
        self.alpha.append(a)
        self.drag.append(d)

        # Calculate v_BN_W, gamma, sigma
        y0 = [self.v_BN_W[-1], self.gamma[-1], self.sigma[-1]]
        sol = solve_ivp(self.__eom_ode, [self.time[-1], self.time[-1] + dt], y0, method='RK45')
        self.v_BN_W.append(sol.y[0][-1])
        self.gamma.append(sol.y[1][-1])
        self.sigma.append(sol.y[2][-1])

        # Calculate airspeed
        self.airspeed.append(utils.wind_vector(self.v_BN_W[-1], self.gamma[-1], self.sigma[-1]))

        # Convert to ECEF
        y0 = [self.lat[-1], self.lon[-1], self.h[-1]]
        sol = solve_ivp(self.__ecef_ode, [self.time[-1], self.time[-1] + dt], y0, method='RK45')
        self.lat.append(sol.y[0][-1])
        self.lon.append(sol.y[1][-1])
        self.h.append(sol.y[2][-1])

    def _thrustGuidanceSystem(self, dt):
        xT_old = self.xT
        self.V_err = self.command.v_BN_W - self.v_BN_W[-1]  # Calculate inertial velocity error

        # Evaluate ODE x_T_dot = m*V_err via RK45 to receive x_T for new velocity error
        sol = solve_ivp(self.__xT_dot_ode, [self.time[-1], self.time[-1] + dt], [xT_old], method='RK45')
        self.xT = sol.y[-1][-1]

        # Use xT in calculation of Thrust command
        self.Tc = self.K_Ti*self.xT + self.K_Tp*self.mass[-1]*self.V_err

        # Saturation of thrust command
        if self.Tc > self.Vehicle.T_max:
            print(f'Commanded thrust {self.Tc} exceeds max thrust {self.Vehicle.T_max}')
            self.Tc = self.Vehicle.T_max

        sol = solve_ivp(self.__T_dot_ode, [self.time[-1], self.time[-1] + dt], [self.Thrust[-1]], method='RK45')
        self.Thrust.append(sol.y[-1][-1])

        # Saturation of vehicle thrust
        if self.Thrust[-1] > self.Vehicle.T_max:
            self.Thrust[-1] = self.Vehicle.T_max

        return self.Tc, self.Thrust[-1]

    def _liftGuidanceSystem(self, dt):
        # Step 1: Calculate max lift (L_max)
        # Inputs: v_BN_W (Current aircraft inertial velocity)
        # Outputs: L_max (maximum lift)
        L_max = self.v_BN_W[-1]**2 * self.Vehicle.K_Lmax

        # Calculate commanded lift (L_c)
        xL_old = self.xL
        self.hdot_err = self.command.v_BN_W*(np.sin(self.command.gamma) - np.sin(self.gamma[-1]))
        # Evaluate ODE x_L_dot = m*h_dot_err via RK45 to receive x_L for Lift Command calculation
        sol = solve_ivp(self.__xL_dot_ode, [self.time[-1], self.time[-1] + dt], [xL_old], method='RK45')
        self.xL = sol.y[-1][-1]
        self.Lc = self.K_Li*self.xL + self.K_Lp*self.mass[-1]*self.hdot_err

        # Saturation (upper/lower limits on commanded lift)
        if self.Lc > L_max:
            print(f'Command lift {self.Lc} is greater than max lift {L_max}, setting to {L_max}')
            self.Lc = L_max

        # Calculate lift
        sol = solve_ivp(self.__L_dot_ode, [self.time[-1], self.time[-1] + dt], [self.Lift[-1]], method='RK45')
        self.Lift.append(sol.y[-1][-1])

        if self.Lift[-1] > L_max:
            self.Lift[-1] = L_max

        # Calculate commanded angle of attack (alpha_c)
        alpha_c = 2 * self.Lc / (utils.const_density * self.Vehicle.wing_area * self.Vehicle.C_Lalpha * self.airspeed[-1]**2) + self.Vehicle.alpha_o
        self.alpha_c.append(alpha_c)

        # Calculate altitude command (h_c)
        h_c = np.sin(self.command.gamma) * self.command.v_BN_W * (self.time[-1] + dt) + self.h[0]
        self.h_c.append(h_c)

        return self.Lift[-1], alpha_c, h_c

    def _headingGuidanceSystem(self, dt):
        # NOTE mu in code equals Phi_W in book (wind-axes bank angle)
        # NOTE sigma in code equals Psi_W in book (heading)
        self.sigma_err = self.command.sigma - self.sigma[-1]
        mu = self._calculateMu()

        if np.abs(mu) > self.Vehicle.mu_max:
            print(f'Command bank angle {mu} exceeds max allowable bank angle |{self.Vehicle.mu_max}|')
            mu = np.sign(mu) * self.Vehicle.mu_max

        self.mu.append(mu)

        return mu

    def _calculateAlpha(self):
        return ((2*self.Lift[-1]) / (utils.const_density * self.Vehicle.wing_area * self.Vehicle.C_Lalpha * self.airspeed[-1]**2)) + self.Vehicle.alpha_o

    def _calculateDrag(self):
        return 0.5 * utils.const_density * self.Vehicle.wing_area * self.Vehicle.C_Do * self.airspeed[-1]**2 + (2 * self.Lift[-1]**2) / (utils.const_density * self.Vehicle.wing_area * np.pi * self.Vehicle.aspect_ratio * self.Vehicle.wing_eff * self.airspeed[-1]**2)

    def _calculateMu(self):
        return self.K_mu_p*(self.command.v_BN_W / utils.const_gravity) * self.sigma_err

    def __xT_dot_ode(self, t, xT=0): return self.mass[-1] * self.V_err

    def __T_dot_ode(self, t, T): return -1*self.Vehicle.omega_T*T + self.Vehicle.omega_T*self.Tc

    def __xL_dot_ode(self, t, xL): return self.mass[-1] * self.hdot_err

    def __L_dot_ode(self, t, L): return -1*self.Vehicle.omega_L*L + self.Vehicle.omega_L*self.Lc

    def __m_dot_ode(self, t, m): return -1*self.Vehicle.Kf * self.Thrust[-1]

    def __eom_ode(self, t, y0):
        # y0 = [v_BN_W, gamma, sigma]
        v_BN_W_dot = ((self.Thrust[-1] - self.drag[-1]) / self.mass[-1]) - utils.const_gravity * np.sin(self.gamma[-1])
        gamma_dot = (1/self.v_BN_W[-1]) * ((self.Lift[-1] * np.cos(self.mu[-1])/self.mass[-1]) - utils.const_gravity * np.cos(self.gamma[-1]))
        sigma_dot = (1/(self.v_BN_W[-1] * np.cos(self.gamma[-1]))) * (self.Lift[-1] * np.sin(self.mu[-1]) / self.mass[-1])
        return [v_BN_W_dot, gamma_dot, sigma_dot]

    def __ecef_ode(self, t, y0):
        # y0 = [lat, lon, h]
        lat_dot = self.v_BN_W[-1] * np.cos(self.gamma[-1]) * np.cos(self.sigma[-1]) / (utils.Re_bar + self.h[-1])
        lon_dot = self.v_BN_W[-1] * np.cos(self.gamma[-1]) * np.sin(self.sigma[-1]) / ((utils.Re_bar + self.h[-1]) * np.cos(self.lat[-1]))
        h_dot = self.v_BN_W[-1] * np.sin(self.gamma[-1])
        return [lat_dot, lon_dot, h_dot]


def run_FW_UAV_GNC_Test(stopTime, loadSimulationFilePath=None, saveSimulationFilePath=None, saveFiguresFolderPath=None):
    # If a previously-run simulation .pkl file is supplied, load that instead of running a new simulation.
    if loadSimulationFilePath is not None:
        print(f'Loading saved simulation data from <{loadSimulationFilePath}>')
        acft_Guidance = utils.load_obj(loadSimulationFilePath)

    # Otherwise, run a simulation and save the results to a .pkl file if a saveSimulationFilePath is given.
    else:
        # Define the aircraft (example aircraft is C-130)
        new_aircraft_parameters = {'weight_max': 327000,
                                   'weight_min': 157000,
                                   'speed_max': 600 * utils.mph2fps,
                                   'speed_min': 200 * utils.mph2fps,
                                   'Kf': 4e-6,
                                   'omega_T': 2,
                                   'omega_L': 2.5,
                                   'omega_mu': 1,
                                   'T_max': 72000,
                                   'K_Lmax': 2.6,
                                   'mu_max': 30 * utils.d2r,
                                   'C_Do': 0.0183,
                                   'C_Lalpha': 0.0920 / utils.d2r,
                                   'alpha_o': -0.05 * utils.d2r,
                                   'wing_area': 1745,
                                   'aspect_ratio': 10.1,
                                   'wing_eff': 0.613}

        # Build the aircraft object
        with utils.Timer('build_acft_obj'):
            my_acft = FixedWingVehicle(new_aircraft_parameters)

        # Define the aircraft's initial conditions
        init_cond = {'v_BN_W': 400 * utils.mph2fps,
                    'h': 0,
                    'gamma': 0,
                    'sigma': 0,
                    'lat': 33.2098 * utils.d2r,
                    'lon': -87.5692 * utils.d2r,
                    'v_WN_N': [25 * utils.mph2fps, 25 * utils.mph2fps, 0],
                    'weight': 300000}

        # PI Guidance Transfer Functions
        TF_constants = {'K_Tp': 0.08, 'K_Ti': 0.002, 'K_Lp': 0.5, 'K_Li': 0.01, 'K_mu_p': 0.075}

        # Build the guidance system using the aircraft object and control system transfer function constants
        with utils.Timer('build_GuidanceSystem_obj'):
            acft_Guidance = FW_NL_GuidanceSystem(my_acft, TF_constants, init_cond)
        
        with utils.Timer('run_FW_UAV_GNC_Test'):
            while acft_Guidance.time[-1] < stopTime:
                if acft_Guidance.time[-1] >= 1 and acft_Guidance.time[-2] < 1:
                    # Give the aircraft a command
                    # velocity = 450 mph
                    # rate_of_climb = 5 degrees
                    # heading = 15 degrees (NNE)
                    acft_Guidance.setCommandTrajectory(450 * utils.mph2fps, 5 * utils.d2r, 15 * utils.d2r)
                acft_Guidance.stepTime()

        if saveSimulationFilePath is not None:
            with utils.Timer('save_obj'):
                utils.save_obj(acft_Guidance, saveSimulationFilePath)

    # Show/save the plots from the simulation
    if saveFiguresFolderPath is None:
        utils.plotSim(acft_Guidance, showPlots=True)
    else:
        utils.plotSim(acft_Guidance, saveFolder=saveFiguresFolderPath, showPlots=False)

    return


if __name__ == '__main__':
    # Run through simulation -- Note that commands are set at 1s
    # run_FW_UAV_GNC_Test(120, loadSimulationFilePath=r'.\saved_simulations\120s_C130_1s_command_run_FW_UAV_GNC_test_C130.pkl', saveFiguresFolderPath=r'.\saved_simulations\figures\120 second C130 simulation')
    run_FW_UAV_GNC_Test(15)