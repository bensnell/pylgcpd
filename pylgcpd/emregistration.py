from __future__ import division
import numbers
from warnings import warn
import numpy as np
from .utility import to_numpy, import_cupy_xp
cp, xp = import_cupy_xp()


def initialize_sigma2(X, Y):
    (N, D) = X.shape
    (M, _) = Y.shape
    diff = X[None, :, :] - Y[:, None, :]
    err = diff ** 2
    return xp.sum(err) / (D * M * N)

def lowrankQS(G, beta, num_eig, eig_fgt=False):
    # if we do not use FGT we construct affinity matrix G and find the
    # first eigenvectors/values directly

    if eig_fgt is False:
        S, Q = xp.linalg.eigh(G)
        eig_indices = list(xp.argsort(xp.abs(S))[::-1][:num_eig])
        Q = Q[:, eig_indices]  # eigenvectors
        S = S[eig_indices]  # eigenvalues.

        return Q, S

    elif eig_fgt is True:
        raise Exception('Fast Gauss Transform Not Implemented!')

class EMRegistration(object):
    """
    Expectation maximization point cloud registration.

    Attributes
    ----------
    X_points: numpy array
        NxD array of target points.

    X_landmarks: numpy array
        KxD array of target landmarks

    X_points_and_landmarks: numpy array
        (N+K)xD array of target points and landmarks (concatenated)

    Y_points: numpy array
        MxD array of source points.

    Y_landmarks: numpy array
        KxD array of source landmarks

    Y_points_and_landmarks: numpy array
        (M+K)xD array of source points and landmarks (concatenated)

    TY_points_and_landmarks: numpy array
        (M+K)xD array of transformed source points (and landmarks at end).
        To access just source points, use `TY_points_and_landmarks[:M]`

    sigma2: float (positive)
        Initial variance of the Gaussian mixture model.

    N: int
        Number of target points.

    M: int
        Number of source points.

    D: int
        Dimensionality of source and target points.

    K: int
        Number of landmarks (0 if not landmark-guided).

    iteration: int
        The current iteration throughout registration.

    max_iterations: int or None
        Registration will terminate once the algorithm has taken this
        many iterations. If None, the algorithm stops when the tolerance is met.

    force_max_iterations: boolean
        Value indicates whether algorithm must complete max_iterations iterations before
        stopping. 

    tolerance: float (positive)
        Registration will terminate once the difference between
        consecutive objective function values falls within this tolerance.

    w: float (between 0 and 1)
        Contribution of the uniform distribution to account for outliers.
        Valid values span 0 (inclusive) and 1 (exclusive).

    q: float
        The objective function value that represents the misalignment between source
        and target point clouds.

    diff: float (positive)
        The absolute difference between the current and previous objective function values.

    P: numpy array
        (M+K)x(N+K) array of probabilities.
        P[m, n] represents the probability that the m-th source point
        corresponds to the n-th target point.

    Pt1: numpy array
        (N+K)x1 column array.
        Multiplication result between the transpose of P and a column vector of all 1s.

    P1: numpy array
        (M+K)x1 column array.
        Multiplication result between P and a column vector of all 1s.

    Np_with_landmarks: float (positive)
        The sum of all elements in P, including landmarks.

    Np_without_landmarks: float (positive)
        The sum of all elements in P, excluding landmarks.

    landmark_guided: boolean
        Is this a guided registration?

    ss2: float (positive)
        "Sigma Starred Squared"
        Describes the influence of landmarks. 
        (The smaller the value is set for σ*2, the stronger the constraints on the corresponding landmarks.)

    normalize: boolean
        Should this registration object normalize the inputs (& denormalize on output)?
        This is highly recommended, but is false by default.

    normalize_joint: boolean
        Should the inputs be normalized together (dependently), as opposed to independently?
        This variable only applies if normalize=True. By default, this variable is False.

    verbose: boolean (default: True)
        Should debugging information be printed to the console?

    """

    def __init__(self, 
        X, 
        Y, 
        sigma2=None, 
        max_iterations=None, 
        tolerance=None, 
        outliers=None, 
        ss2=None,
        X_landmarks=None,
        Y_landmarks=None,
        normalize=None,
        normalize_joint=None,
        verbose=True,
        force_max_iterations=None,
        *args, **kwargs):

        # Convert the inputs to arrays on the correct device (CPU or GPU).
        X = xp.asarray(X)
        Y = xp.asarray(Y)
        X_landmarks = xp.asarray(X_landmarks)
        Y_landmarks = xp.asarray(Y_landmarks)

        # Set whether verbose information should be printed
        self.verbose = False if verbose is None else verbose

        if type(X) is not xp.ndarray or X.ndim != 2:
            raise ValueError(
                "The target point cloud (X) must be at a 2D numpy array.")

        if type(Y) is not xp.ndarray or Y.ndim != 2:
            raise ValueError(
                "The source point cloud (Y) must be a 2D numpy array.")

        if X.shape[1] != Y.shape[1]:
            raise ValueError(
                "Both point clouds need to have the same number of dimensions.")

        if sigma2 is not None and (not isinstance(sigma2, numbers.Number) or sigma2 <= 0):
            raise ValueError(
                "Expected a positive value for sigma2 instead got: {}".format(sigma2))

        if max_iterations is not None and (not isinstance(max_iterations, numbers.Number) or max_iterations < 0):
            raise ValueError(
                "Expected a positive integer for max_iterations instead got: {}".format(max_iterations))
        elif isinstance(max_iterations, numbers.Number) and not isinstance(max_iterations, int):
            warn("Received a non-integer value for max_iterations: {}. Casting to integer.".format(max_iterations))
            max_iterations = int(max_iterations)

        if tolerance is not None and (not isinstance(tolerance, numbers.Number) or tolerance < 0):
            raise ValueError(
                "Expected a positive float for tolerance instead got: {}".format(tolerance))

        if outliers is not None and (not isinstance(outliers, numbers.Number) or outliers < 0 or outliers >= 1):
            raise ValueError(
                "Expected a value between 0 (inclusive) and 1 (exclusive) for w instead got: {}".format(outliers))

        if ss2 is not None and (not isinstance(ss2, numbers.Number) or ss2 <= 0):
            raise ValueError(
                "Expected a positive value for ss2. Instead got: {}".format(ss2))
        
        # Is this landmark-guided?
        self.landmark_guided = X_landmarks is not None and Y_landmarks is not None \
            and X_landmarks.shape[0] != 0 and X_landmarks.shape == Y_landmarks.shape

        if self.landmark_guided:
            if self.verbose: print("Enabling landmark-guided registration.")

        if X_landmarks is not None and len(X_landmarks)==0:
            raise ValueError(
                "Expected array of nonzero length for X_landmarks. Instead got: {}".format(X_landmarks))
        
        if Y_landmarks is not None and len(Y_landmarks)==0:
            raise ValueError(
                "Expected array of nonzero length for Y_landmarks. Instead got: {}".format(Y_landmarks))

        if X_landmarks is not None and Y_landmarks is not None and X_landmarks.shape != Y_landmarks.shape:
            raise ValueError(
                "Landmark arrays must be the same shape. Cannot enable landmark-guided registration.")

        # Target points (no landmarks)
        self.X_points = X
        # Source points (no landmarks)
        self.Y_points = Y

        # Quantities & dimensionalities of points
        (self.N, self.D) = self.X_points.shape
        (self.M, _) = self.Y_points.shape

        # Landmark-guided hyper-parameter (What should default be?)
        self.ss2 = 1e-1 if ss2 is None else ss2
        # Number of landmarks
        self.K = 0 if not self.landmark_guided else X_landmarks.shape[0]
        # Landmarks
        if self.landmark_guided:
            self.X_landmarks = X_landmarks
            self.Y_landmarks = Y_landmarks
        else:
            self.X_landmarks = xp.zeros((0,self.D))
            self.Y_landmarks = xp.zeros((0,self.D))
        # Points and landmarks concatenated
        self.X_points_and_landmarks = xp.concatenate([self.X_points, self.X_landmarks])
        self.Y_points_and_landmarks = xp.concatenate([self.Y_points, self.Y_landmarks])

        # Transformed source points (and landmarks) (deep copy)
        self.TY_points_and_landmarks = xp.copy(self.Y_points_and_landmarks)

        # Are we normalizing the inputs?
        self.normalize = False if normalize is None else normalize
        # Are we jointly normalizing?
        self.normalize_joint = False if normalize_joint is None else normalize_joint
        # Calculate all normalization params and apply normalization if present
        self.calculateNormalizationParams()
        self.normalizeData()

        # Iterations
        self.max_iterations = max_iterations
        self.force_max_iterations = False if (force_max_iterations is None or max_iterations is None) else force_max_iterations
        self.iteration = 0
        self.diff = xp.inf

        # Tolerance
        self.tolerance = 0.001 if tolerance is None else tolerance

        # Outlier influence
        # (Default is 0.1 in matlab code)
        self.outliers = 0.0 if outliers is None else outliers

        # Initial variance of GMM
        self.sigma2 = initialize_sigma2(self.X_points, self.Y_points) if sigma2 is None else sigma2

        # Other matricies used mostly in the expectation step.
        # Their sizes are correct, but I'm not 100% sure
        # what the scalars represent.
        self.q = xp.inf
        self.P = xp.zeros((self.M + self.K, self.N + self.K))
        self.Pt1 = xp.zeros((self.N + self.K, ))
        self.P1 = xp.zeros((self.M + self.K, ))
        self.PX = xp.zeros((self.M + self.K, self.D))
        self.Np_with_landmarks = 0
        self.Np_without_landmarks = 0

    def calculateNormalizationParams(self):

        # Set default values
        X_mean = xp.zeros(self.D)
        Y_mean = xp.zeros(self.D)
        X_scale = 1.0
        Y_scale = 1.0

        if self.normalize:

            # Calculate the mean
            if self.normalize_joint:
                X_mean = Y_mean = xp.mean(xp.vstack([self.X_points_and_landmarks, self.Y_points_and_landmarks]), axis=0)
            else:
                X_mean = xp.mean(self.X_points_and_landmarks, axis=0)
                Y_mean = xp.mean(self.Y_points_and_landmarks, axis=0)

            X = self.X_points_and_landmarks - X_mean
            Y = self.Y_points_and_landmarks - Y_mean

            # Calculate the scale
            if self.normalize_joint:
                X_scale = Y_scale = xp.sqrt(xp.sum(xp.sum(xp.power(xp.vstack([X,Y]),2), axis=1))/(len(X)+len(Y)))
            else:
                X_scale = xp.sqrt(xp.sum(xp.sum(xp.power(X,2), axis=1))/len(X))
                Y_scale = xp.sqrt(xp.sum(xp.sum(xp.power(Y,2), axis=1))/len(Y))

        # Save these params
        self.normalize_params = {
            'X_mean' : X_mean,
            'Y_mean' : Y_mean,
            'X_scale' : X_scale,
            'Y_scale' : Y_scale
        }

        # Define normalization functions (nondestructive)
        def normalize(data, mode, invert=False):

            # Get the appropriate mean and scale based on the type provided
            mean = X_mean if mode == 'X' else Y_mean
            scale = X_scale if mode == 'X' else Y_scale

            # We want this function to be able to be used across both numpy and cupy arrays,
            # so modify the mean and scale to align with the mode of `data`.
            if type(data) == np.ndarray:
                mean = to_numpy(mean)
                scale = to_numpy(scale)

            # Return the normalized data
            if invert:
                return data * scale + mean
            else:
                return (data - mean) / scale

        def denormalize(data, mode):
            return normalize(data, mode, True)

        # Store these functions
        self.normalize_fncts = {
            'normalize' : normalize,
            'denormalize' : denormalize
        }

    # Normalize all source and target data (points and landmarks)
    def normalizeData(self):

        # Retrieve the normalization function
        normalize = self.normalize_fncts['normalize']

        # Set all X and Y parameters
        self.X_points = normalize(self.X_points, 'X')
        self.X_landmarks = normalize(self.X_landmarks, 'X')
        self.X_points_and_landmarks = normalize(self.X_points_and_landmarks, 'X')

        self.Y_points = normalize(self.Y_points, 'Y')
        self.Y_landmarks = normalize(self.Y_landmarks, 'Y')
        self.Y_points_and_landmarks = normalize(self.Y_points_and_landmarks, 'Y')
        self.TY_points_and_landmarks = normalize(self.TY_points_and_landmarks, 'Y')

    # Data returned should be in numpy format
    def register(self, callback=lambda **kwargs: None):
        self.transform_point_cloud()
        # Should we include an additional check for sigma2 > 1e-8 here?
        while (self.max_iterations is None or self.iteration < self.max_iterations) and \
            ((self.max_iterations is not None and self.force_max_iterations) or self.diff > self.tolerance):
            self.iterate()
            if callable(callback):
                kwargs = {'iteration': self.iteration,
                          'error': self.q, 'X': self.X_points, 'Y': self.TY_points_and_landmarks[:self.M]}
                callback(**kwargs)

        return self.get_transformed_points(), self.get_registration_parameters()
    
    # Data returned should be in numpy format
    def get_transformed_points(self):
        if self.normalize:
            denormalize = self.normalize_fncts['denormalize']
            # It seems counterintuitive to denormalize with the 'X' (not 'Y' params),
            # but this is exactly what backprojects the normalized TY data into the 
            # target 'X' space. This is correct as is.
            return to_numpy(denormalize(self.TY_points_and_landmarks[:self.M], 'X'))
        else:
            return to_numpy(self.TY_points_and_landmarks[:self.M])

    # Data returned should be in numpy format
    def get_transformed_landmarks(self):
        if self.normalize:
            denormalize = self.normalize_fncts['denormalize']
            return to_numpy(denormalize(self.TY_points_and_landmarks[self.M:], 'X'))
        else:
            return to_numpy(self.TY_points_and_landmarks[self.M:])

    # Data returned should be in numpy format
    def get_registration_parameters(self):
        # Remember to denormalize here
        raise NotImplementedError(
            "Registration parameters should be defined in child classes.")

    # The function should be expected to work on numpy arrays
    def get_transformation_function(self):
        # Remember to use denormalized params
        raise NotImplementedError(
            "Transformation function should be defined in child classes.")

    def update_transform(self):
        raise NotImplementedError(
            "Updating transform parameters should be defined in child classes.")

    def transform_point_cloud(self):
        raise NotImplementedError(
            "Updating the source point cloud should be defined in child classes.")

    def update_variance(self):
        raise NotImplementedError(
            "Updating the Gaussian variance for the mixture model should be defined in child classes.")

    def update_hyperparameters(self):
        # This is called before each iteration.
        # Use this function to update any hyperparameters that change in value each loop.
        None

    def iterate(self):
        self.update_hyperparameters()
        self.expectation()
        self.maximization()
        self.iteration += 1

    # [same]
    def expectation(self):
        
        # Calculate Pmn. Don't worry about landmarks at the moment.
        # Begin calculating the Pmn matrix. 
        P = xp.sum((self.X_points[None, :, :] - self.TY_points_and_landmarks[:self.M][:, None, :]) ** 2, axis=2)
        
        # Apply exponent to Pmn
        P = xp.exp(-P / (2 * self.sigma2))

        # Calculate the constant right hand side of the expression
        # in the denominator for Pmn. We call this variable `c` for  "const".
        c = (2 * xp.pi * self.sigma2) ** (self.D / 2)
        c = c * self.outliers / (1 - self.outliers)
        c = c * self.M / self.N

        # Calculate the full denominator
        den = xp.sum(P, axis=0) + c
        den = xp.tile(den, (self.M, 1))
        den[den == 0] = xp.finfo(float).eps

        # Calculate Pmn. This completes the expectation step for non-guided.
        P = xp.divide(P, den)

        # Now, consider the effect of landmarks.
        if self.landmark_guided:
            # Increase the size of P to account for the interaction between landmarks
            P = xp.pad(P, ((0,self.K),(0,self.K)), 'constant', constant_values=(0))
            # Make the landmark sub-matrix an identity matrix, where
            # the diagonals are sigma2/ss2.
            P[self.M:,self.N:] = xp.identity(self.K) * self.sigma2/self.ss2
        
        self.P = P
        self.Pt1 = xp.sum(self.P, axis=0)
        self.P1 = xp.sum(self.P, axis=1) 
        self.Np_with_landmarks = xp.sum(self.P1)
        self.Np_without_landmarks = xp.sum(self.P1[:self.M])
        self.PX = xp.matmul(self.P, self.X_points_and_landmarks)

    def maximization(self):
        self.update_transform()
        self.transform_point_cloud()
        self.update_variance()
