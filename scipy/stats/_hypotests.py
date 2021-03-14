from collections import namedtuple
from dataclasses import make_dataclass
import numpy as np
import warnings
from itertools import combinations
import scipy.stats
from . import distributions
from ._continuous_distns import chi2, norm
from scipy.special import gamma, kv, gammaln
from . import _wilcoxon_data
import scipy.stats.stats
from functools import wraps


def _remove_nans(samples, paired):
    "Remove nans from pair or unpaired samples"
    # consider usnot copying arrays that don't contain nans
    if not paired:
        return [sample[~np.isnan(sample)] for sample in samples]
        # return [sample[~np.isnan(sample)] if contains_nan else sample
        #         for sample, contains_nan in zip(samples, contains_nans)]
    # else
    nans = np.isnan(samples[0])
    for sample in samples[1:]:
        nans = nans | np.isnan(sample)
    not_nans = ~nans
    return [sample[not_nans] for sample in samples]

def _vectorize_hypotest_factory(result_creator, default_axis=0,
                                n_samples=1, paired=False):
    def vectorize_hypotest_decorator(hypotest_fun_in):
        @wraps(hypotest_fun_in)
        def vectorize_hypotest_wrapper(*args, axis=default_axis,
                                       nan_policy='propagate', **kwds):

            # if n_samples is None, all args are samples
            n_samp = len(args) if n_samples is None else n_samples

            # split samples from other arguments
            samples = [np.atleast_1d(sample) for sample in args[:n_samp]]
            args = args[n_samp:]

            if axis is None:
                samples = [sample.ravel() for sample in samples]
                axis = 0

            # if axis is not needed, just handle nan_policy and return
            ndims = np.array([sample.ndim for sample in samples])
            if np.all(ndims <= 1):
                # Addresses nan_policy="propagate"
                if nan_policy == 'propagate':
                    return hypotest_fun_in(*samples, *args, **kwds)

                # Addresses nan_policy="raise"
                contains_nans = []
                for sample in samples:
                    contains_nan, _ = (
                        scipy.stats.stats._contains_nan(sample, nan_policy))
                    contains_nans.append(contains_nan)

                # Addresses nan_policy="omit" (doesn't get here otherwise)
                if any(contains_nans):
                    # consider passing in contains_nans
                    samples = _remove_nans(samples, paired)

                return hypotest_fun_in(*samples, *args, **kwds)

            # otherwise, concatenate all samples along axis, remembering where
            # each separate sample begins
            lengths = np.array([sample.shape[axis] for sample in samples])
            split_indices = np.cumsum(lengths)
            x = scipy.stats.stats._broadcast_concatenate(samples, axis)

            # Addresses nan_policy="raise"
            contains_nan, _ = (
                scipy.stats.stats._contains_nan(x, nan_policy))

            # Addresses nan_policy="omit"
            # Assumes nan_policy="propagate" is handled by hypotest_fun_in
            def hypotest_fun(x):
                samples = np.split(x, split_indices)[:n_samp]
                if nan_policy == 'omit' and contains_nan:
                    samples = _remove_nans(samples, paired)
                return hypotest_fun_in(*samples, *args, **kwds)

            if np.all(ndims == 1) is None:
                return hypotest_fun(x)
            else:
                x = np.moveaxis(x, axis, -1)  # needed by result_creator
                res = np.apply_along_axis(hypotest_fun, axis=-1, arr=x)
                return result_creator(res)

        return vectorize_hypotest_wrapper
    return vectorize_hypotest_decorator


Epps_Singleton_2sampResult = namedtuple('Epps_Singleton_2sampResult',
                                        ('statistic', 'pvalue'))


@_vectorize_hypotest_factory(
    result_creator=lambda res: Epps_Singleton_2sampResult(res[..., 0],
                                                          res[..., 1]),
    n_samples=2)
def epps_singleton_2samp(x, y, t=(0.4, 0.8)):
    """
    Compute the Epps-Singleton (ES) test statistic.

    Test the null hypothesis that two samples have the same underlying
    probability distribution.

    Parameters
    ----------
    x, y : array-like
        The two samples of observations to be tested. Input must not have more
        than one dimension. Samples can have different lengths.
    t : array-like, optional
        The points (t1, ..., tn) where the empirical characteristic function is
        to be evaluated. It should be positive distinct numbers. The default
        value (0.4, 0.8) is proposed in [1]_. Input must not have more than
        one dimension.

    Returns
    -------
    statistic : float
        The test statistic.
    pvalue : float
        The associated p-value based on the asymptotic chi2-distribution.

    See Also
    --------
    ks_2samp, anderson_ksamp

    Notes
    -----
    Testing whether two samples are generated by the same underlying
    distribution is a classical question in statistics. A widely used test is
    the Kolmogorov-Smirnov (KS) test which relies on the empirical
    distribution function. Epps and Singleton introduce a test based on the
    empirical characteristic function in [1]_.

    One advantage of the ES test compared to the KS test is that is does
    not assume a continuous distribution. In [1]_, the authors conclude
    that the test also has a higher power than the KS test in many
    examples. They recommend the use of the ES test for discrete samples as
    well as continuous samples with at least 25 observations each, whereas
    `anderson_ksamp` is recommended for smaller sample sizes in the
    continuous case.

    The p-value is computed from the asymptotic distribution of the test
    statistic which follows a `chi2` distribution. If the sample size of both
    `x` and `y` is below 25, the small sample correction proposed in [1]_ is
    applied to the test statistic.

    The default values of `t` are determined in [1]_ by considering
    various distributions and finding good values that lead to a high power
    of the test in general. Table III in [1]_ gives the optimal values for
    the distributions tested in that study. The values of `t` are scaled by
    the semi-interquartile range in the implementation, see [1]_.

    References
    ----------
    .. [1] T. W. Epps and K. J. Singleton, "An omnibus test for the two-sample
       problem using the empirical characteristic function", Journal of
       Statistical Computation and Simulation 26, p. 177--203, 1986.

    .. [2] S. J. Goerg and J. Kaiser, "Nonparametric testing of distributions
       - the Epps-Singleton two-sample test using the empirical characteristic
       function", The Stata Journal 9(3), p. 454--465, 2009.

    """

    x, y, t = np.asarray(x), np.asarray(y), np.asarray(t)
    # check if x and y are valid inputs
    if x.ndim > 1:
        raise ValueError('x must be 1d, but x.ndim equals {}.'.format(x.ndim))
    if y.ndim > 1:
        raise ValueError('y must be 1d, but y.ndim equals {}.'.format(y.ndim))
    nx, ny = len(x), len(y)
    if (nx < 5) or (ny < 5):
        raise ValueError('x and y should have at least 5 elements, but len(x) '
                         '= {} and len(y) = {}.'.format(nx, ny))
    if not np.isfinite(x).all():
        raise ValueError('x must not contain nonfinite values.')
    if not np.isfinite(y).all():
        raise ValueError('y must not contain nonfinite values.')
    n = nx + ny

    # check if t is valid
    if t.ndim > 1:
        raise ValueError('t must be 1d, but t.ndim equals {}.'.format(t.ndim))
    if np.less_equal(t, 0).any():
        raise ValueError('t must contain positive elements only.')

    # rescale t with semi-iqr as proposed in [1]; import iqr here to avoid
    # circular import
    from scipy.stats import iqr
    sigma = iqr(np.hstack((x, y))) / 2
    ts = np.reshape(t, (-1, 1)) / sigma

    # covariance estimation of ES test
    gx = np.vstack((np.cos(ts*x), np.sin(ts*x))).T  # shape = (nx, 2*len(t))
    gy = np.vstack((np.cos(ts*y), np.sin(ts*y))).T
    cov_x = np.cov(gx.T, bias=True)  # the test uses biased cov-estimate
    cov_y = np.cov(gy.T, bias=True)
    est_cov = (n/nx)*cov_x + (n/ny)*cov_y
    est_cov_inv = np.linalg.pinv(est_cov)
    r = np.linalg.matrix_rank(est_cov_inv)
    if r < 2*len(t):
        warnings.warn('Estimated covariance matrix does not have full rank. '
                      'This indicates a bad choice of the input t and the '
                      'test might not be consistent.')  # see p. 183 in [1]_

    # compute test statistic w distributed asympt. as chisquare with df=r
    g_diff = np.mean(gx, axis=0) - np.mean(gy, axis=0)
    w = n*np.dot(g_diff.T, np.dot(est_cov_inv, g_diff))

    # apply small-sample correction
    if (max(nx, ny) < 25):
        corr = 1.0/(1.0 + n**(-0.45) + 10.1*(nx**(-1.7) + ny**(-1.7)))
        w = corr * w

    p = chi2.sf(w, r)

    return Epps_Singleton_2sampResult(w, p)


class CramerVonMisesResult:
    def __init__(self, statistic, pvalue):
        self.statistic = statistic
        self.pvalue = pvalue

    def __repr__(self):
        return (f"{self.__class__.__name__}(statistic={self.statistic}, "
                f"pvalue={self.pvalue})")


def _psi1_mod(x):
    """
    psi1 is defined in equation 1.10 in Csorgo, S. and Faraway, J. (1996).
    This implements a modified version by excluding the term V(x) / 12
    (here: _cdf_cvm_inf(x) / 12) to avoid evaluating _cdf_cvm_inf(x)
    twice in _cdf_cvm.

    Implementation based on MAPLE code of Julian Faraway and R code of the
    function pCvM in the package goftest (v1.1.1), permission granted
    by Adrian Baddeley. Main difference in the implementation: the code
    here keeps adding terms of the series until the terms are small enough.
    """

    def _ed2(y):
        z = y**2 / 4
        b = kv(1/4, z) + kv(3/4, z)
        return np.exp(-z) * (y/2)**(3/2) * b / np.sqrt(np.pi)

    def _ed3(y):
        z = y**2 / 4
        c = np.exp(-z) / np.sqrt(np.pi)
        return c * (y/2)**(5/2) * (2*kv(1/4, z) + 3*kv(3/4, z) - kv(5/4, z))

    def _Ak(k, x):
        m = 2*k + 1
        sx = 2 * np.sqrt(x)
        y1 = x**(3/4)
        y2 = x**(5/4)

        e1 = m * gamma(k + 1/2) * _ed2((4 * k + 3)/sx) / (9 * y1)
        e2 = gamma(k + 1/2) * _ed3((4 * k + 1) / sx) / (72 * y2)
        e3 = 2 * (m + 2) * gamma(k + 3/2) * _ed3((4 * k + 5) / sx) / (12 * y2)
        e4 = 7 * m * gamma(k + 1/2) * _ed2((4 * k + 1) / sx) / (144 * y1)
        e5 = 7 * m * gamma(k + 1/2) * _ed2((4 * k + 5) / sx) / (144 * y1)

        return e1 + e2 + e3 + e4 + e5

    x = np.asarray(x)
    tot = np.zeros_like(x, dtype='float')
    cond = np.ones_like(x, dtype='bool')
    k = 0
    while np.any(cond):
        z = -_Ak(k, x[cond]) / (np.pi * gamma(k + 1))
        tot[cond] = tot[cond] + z
        cond[cond] = np.abs(z) >= 1e-7
        k += 1

    return tot


def _cdf_cvm_inf(x):
    """
    Calculate the cdf of the Cramér-von Mises statistic (infinite sample size).

    See equation 1.2 in Csorgo, S. and Faraway, J. (1996).

    Implementation based on MAPLE code of Julian Faraway and R code of the
    function pCvM in the package goftest (v1.1.1), permission granted
    by Adrian Baddeley. Main difference in the implementation: the code
    here keeps adding terms of the series until the terms are small enough.

    The function is not expected to be accurate for large values of x, say
    x > 4, when the cdf is very close to 1.
    """
    x = np.asarray(x)

    def term(x, k):
        # this expression can be found in [2], second line of (1.3)
        u = np.exp(gammaln(k + 0.5) - gammaln(k+1)) / (np.pi**1.5 * np.sqrt(x))
        y = 4*k + 1
        q = y**2 / (16*x)
        b = kv(0.25, q)
        return u * np.sqrt(y) * np.exp(-q) * b

    tot = np.zeros_like(x, dtype='float')
    cond = np.ones_like(x, dtype='bool')
    k = 0
    while np.any(cond):
        z = term(x[cond], k)
        tot[cond] = tot[cond] + z
        cond[cond] = np.abs(z) >= 1e-7
        k += 1

    return tot


def _cdf_cvm(x, n=None):
    """
    Calculate the cdf of the Cramér-von Mises statistic for a finite sample
    size n. If N is None, use the asymptotic cdf (n=inf)

    See equation 1.8 in Csorgo, S. and Faraway, J. (1996) for finite samples,
    1.2 for the asymptotic cdf.

    The function is not expected to be accurate for large values of x, say
    x > 2, when the cdf is very close to 1 and it might return values > 1
    in that case, e.g. _cdf_cvm(2.0, 12) = 1.0000027556716846.
    """
    x = np.asarray(x)
    if n is None:
        y = _cdf_cvm_inf(x)
    else:
        # support of the test statistic is [12/n, n/3], see 1.1 in [2]
        y = np.zeros_like(x, dtype='float')
        sup = (1./(12*n) < x) & (x < n/3.)
        # note: _psi1_mod does not include the term _cdf_cvm_inf(x) / 12
        # therefore, we need to add it here
        y[sup] = _cdf_cvm_inf(x[sup]) * (1 + 1./(12*n)) + _psi1_mod(x[sup]) / n
        y[x >= n/3] = 1

    if y.ndim == 0:
        return y[()]
    return y


def cramervonmises(rvs, cdf, args=()):
    """
    Perform the one-sample Cramér-von Mises test for goodness of fit.

    This performs a test of the goodness of fit of a cumulative distribution
    function (cdf) :math:`F` compared to the empirical distribution function
    :math:`F_n` of observed random variates :math:`X_1, ..., X_n` that are
    assumed to be independent and identically distributed ([1]_).
    The null hypothesis is that the :math:`X_i` have cumulative distribution
    :math:`F`.

    Parameters
    ----------
    rvs : array_like
        A 1-D array of observed values of the random variables :math:`X_i`.
    cdf : str or callable
        The cumulative distribution function :math:`F` to test the
        observations against. If a string, it should be the name of a
        distribution in `scipy.stats`. If a callable, that callable is used
        to calculate the cdf: ``cdf(x, *args) -> float``.
    args : tuple, optional
        Distribution parameters. These are assumed to be known; see Notes.

    Returns
    -------
    res : object with attributes
        statistic : float
            Cramér-von Mises statistic.
        pvalue : float
            The p-value.

    See Also
    --------
    kstest, cramervonmises_2samp

    Notes
    -----
    .. versionadded:: 1.6.0

    The p-value relies on the approximation given by equation 1.8 in [2]_.
    It is important to keep in mind that the p-value is only accurate if
    one tests a simple hypothesis, i.e. the parameters of the reference
    distribution are known. If the parameters are estimated from the data
    (composite hypothesis), the computed p-value is not reliable.

    References
    ----------
    .. [1] Cramér-von Mises criterion, Wikipedia,
           https://en.wikipedia.org/wiki/Cram%C3%A9r%E2%80%93von_Mises_criterion
    .. [2] Csorgo, S. and Faraway, J. (1996). The Exact and Asymptotic
           Distribution of Cramér-von Mises Statistics. Journal of the
           Royal Statistical Society, pp. 221-234.

    Examples
    --------

    Suppose we wish to test whether data generated by ``scipy.stats.norm.rvs``
    were, in fact, drawn from the standard normal distribution. We choose a
    significance level of alpha=0.05.

    >>> from scipy import stats
    >>> np.random.seed(626)
    >>> x = stats.norm.rvs(size=500)
    >>> res = stats.cramervonmises(x, 'norm')
    >>> res.statistic, res.pvalue
    (0.06342154705518796, 0.792680516270629)

    The p-value 0.79 exceeds our chosen significance level, so we do not
    reject the null hypothesis that the observed sample is drawn from the
    standard normal distribution.

    Now suppose we wish to check whether the same samples shifted by 2.1 is
    consistent with being drawn from a normal distribution with a mean of 2.

    >>> y = x + 2.1
    >>> res = stats.cramervonmises(y, 'norm', args=(2,))
    >>> res.statistic, res.pvalue
    (0.4798693195559657, 0.044782228803623814)

    Here we have used the `args` keyword to specify the mean (``loc``)
    of the normal distribution to test the data against. This is equivalent
    to the following, in which we create a frozen normal distribution with
    mean 2.1, then pass its ``cdf`` method as an argument.

    >>> frozen_dist = stats.norm(loc=2)
    >>> res = stats.cramervonmises(y, frozen_dist.cdf)
    >>> res.statistic, res.pvalue
    (0.4798693195559657, 0.044782228803623814)

    In either case, we would reject the null hypothesis that the observed
    sample is drawn from a normal distribution with a mean of 2 (and default
    variance of 1) because the p-value 0.04 is less than our chosen
    significance level.

    """
    if isinstance(cdf, str):
        cdf = getattr(distributions, cdf).cdf

    vals = np.sort(np.asarray(rvs))

    if vals.size <= 1:
        raise ValueError('The sample must contain at least two observations.')
    if vals.ndim > 1:
        raise ValueError('The sample must be one-dimensional.')

    n = len(vals)
    cdfvals = cdf(vals, *args)

    u = (2*np.arange(1, n+1) - 1)/(2*n)
    w = 1/(12*n) + np.sum((u - cdfvals)**2)

    # avoid small negative values that can occur due to the approximation
    p = max(0, 1. - _cdf_cvm(w, n))

    return CramerVonMisesResult(statistic=w, pvalue=p)


def _get_wilcoxon_distr(n):
    """
    Distribution of counts of the Wilcoxon ranksum statistic r_plus (sum of
    ranks of positive differences).
    Returns an array with the counts/frequencies of all the possible ranks
    r = 0, ..., n*(n+1)/2
    """
    cnt = _wilcoxon_data.COUNTS.get(n)

    if cnt is None:
        raise ValueError("The exact distribution of the Wilcoxon test "
                         "statistic is not implemented for n={}".format(n))

    return np.array(cnt, dtype=int)


def _Aij(A, i, j):
    """Sum of upper-left and lower right blocks of contingency table"""
    # See [2] bottom of page 309
    return A[:i, :j].sum() + A[i+1:, j+1:].sum()


def _Dij(A, i, j):
    """Sum of lower-left and upper-right blocks of contingency table"""
    # See [2] bottom of page 309
    return A[i+1:, :j].sum() + A[:i, j+1:].sum()


def _P(A):
    """Twice the number of concordant pairs, excluding ties."""
    # See [2] bottom of page 309
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*_Aij(A, i, j)
    return count


def _Q(A):
    """Twice the number of discordant pairs, excluding ties."""
    # See [2] bottom of page 309
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*_Dij(A, i, j)
    return count


def _a_ij_Aij_Dij2(A):
    """A term that appears in the ASE of Kendall's tau and Somers' D"""
    # See [2] section 4: Modified ASEs to test the null hypothesis...
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*(_Aij(A, i, j) - _Dij(A, i, j))**2
    return count


def _tau_b(A):
    """Calculate Kendall's tau-b and p-value from contingency table"""
    # See [2] 2.2 and 4.2

    # contingency table must be truly 2D
    if A.shape[0] == 1 or A.shape[1] == 1:
        return np.nan, np.nan

    NA = A.sum()
    PA = _P(A)
    QA = _Q(A)
    Sri2 = (A.sum(axis=1)**2).sum()
    Scj2 = (A.sum(axis=0)**2).sum()
    denominator = (NA**2 - Sri2)*(NA**2 - Scj2)

    tau = (PA-QA)/(denominator)**0.5

    numerator = 4*(_a_ij_Aij_Dij2(A) - (PA - QA)**2 / NA)
    s02_tau_b = numerator/denominator
    if s02_tau_b == 0:  # Avoid divide by zero
        return tau, 0
    Z = tau/s02_tau_b**0.5
    p = 2*norm.sf(abs(Z))  # 2-sided p-value

    return tau, p


def _somers_d(A):
    """Calculate Somers' D and p-value from contingency table"""
    # See [3] page 1740

    # contingency table must be truly 2D
    if A.shape[0] <= 1 or A.shape[1] <= 1:
        return np.nan, np.nan

    NA = A.sum()
    NA2 = NA**2
    PA = _P(A)
    QA = _Q(A)
    Sri2 = (A.sum(axis=1)**2).sum()

    d = (PA - QA)/(NA2 - Sri2)

    S = _a_ij_Aij_Dij2(A) - (PA-QA)**2/NA
    if S == 0:  # Avoid divide by zero
        return d, 0
    Z = (PA - QA)/(4*(S))**0.5
    p = 2*norm.sf(abs(Z))  # 2-sided p-value

    return d, p


SomersDResult = make_dataclass("SomersDResult",
                               ("statistic", "pvalue", "table"))


def somersd(x, y=None):
    r"""
    Calculates Somers' D, an asymmetric measure of ordinal association

    Like Kendall's :math:`\tau`, Somers' :math:`D` is a measure of the
    correspondence between two rankings. Both statistics consider the
    difference between the number of concordant and discordant pairs in two
    rankings :math:`X` and :math:`Y`, and both are normalized such that values
    close  to 1 indicate strong agreement and values close to -1 indicate
    strong disagreement. They differ in how they are normalized. To show the
    relationship, Somers' :math:`D` can be defined in terms of Kendall's
    :math:`\tau_a`:

    .. math::
        D(Y|X) = \frac{\tau_a(X, Y)}{\tau_a(X, X)}

    Suppose the first ranking :math:`X` has :math:`r` distinct ranks and the
    second ranking :math:`Y` has :math:`s` distinct ranks. These two lists of
    :math:`n` rankings can also be viewed as an :math:`r \times s` contingency
    table in which element :math:`i, j` is the number of rank pairs with rank
    :math:`i` in ranking :math:`X` and rank :math:`j` in ranking :math:`Y`.
    Accordingly, `somersd` also allows the input data to be supplied as a
    single, 2D contingency table instead of as two separate, 1D rankings.

    Note that the definition of Somers' :math:`D` is asymmetric: in general,
    :math:`D(Y|X) \neq D(X|Y)`. ``somersd(x, y)`` calculates Somers'
    :math:`D(Y|X)`: the "row" variable :math:`X` is treated as an independent
    variable, and the "column" variable :math:`Y` is dependent. For Somers'
    :math:`D(X|Y)`, swap the input lists or transpose the input table.

    Parameters
    ----------
    x: array_like
        1D array of rankings, treated as the (row) independent variable.
        Alternatively, a 2D contingency table.
    y: array_like
        If `x` is a 1D array of rankings, `y` is a 1D array of rankings of the
        same length, treated as the (column) dependent variable.
        If `x` is 2D, `y` is ignored.

    Returns
    -------
    res : SomersDResult
        A `SomersDResult` object with the following fields:

            correlation : float
               The Somers' :math:`D` statistic.
            pvalue : float
               The two-sided p-value for a hypothesis test whose null
               hypothesis is an absence of association, :math:`D=0`.
               See notes for more information.
            table : 2D array
               The contingency table formed from rankings `x` and `y` (or the
               provided contingency table, if `x` is a 2D array)

    See Also
    --------
    kendalltau : Calculates Kendall's tau, another correlation measure.
    weightedtau : Computes a weighted version of Kendall's tau.
    spearmanr : Calculates a Spearman rank-order correlation coefficient.
    pearsonr : Calculates a Pearson correlation coefficient.

    Notes
    -----
    This function follows the contingency table approach of [2]_ and
    [3]_. *p*-values are computed based on an asymptotic approximation of
    the test statistic distribution under the null hypothesis :math:`D=0`.

    Theoretically, hypothesis tests based on Kendall's :math:`tau` and Somers'
    :math:`D` should be identical.
    However, the *p*-values returned by `kendalltau` are based
    on the null hypothesis of *independence* between :math:`X` and :math:`Y`
    (i.e. the population from which pairs in :math:`X` and :math:`Y` are
    sampled contains equal numbers of all possible pairs), which is more
    specific than the null hypothesis :math:`D=0` used here. If the null
    hypothesis of independence is desired, it is acceptable to use the
    *p*-value returned by `kendalltau` with the statistic returned by
    `somersd` and vice versa. For more information, see [2]_.

    Contingency tables are formatted according to the convention used by
    SAS and R: the first ranking supplied (``x``) is the "row" variable, and
    the second ranking supplied (``y``) is the "column" variable. This is
    opposite the convention of Somers' original paper [1]_.

    References
    ----------
    .. [1] Robert H. Somers, "A New Asymmetric Measure of Association for
           Ordinal Variables", *American Sociological Review*, Vol. 27, No. 6,
           pp. 799--811, 1962.

    .. [2] Morton B. Brown and Jacqueline K. Benedetti, "Sampling Behavior of
           Tests for Correlation in Two-Way Contingency Tables", *Journal of
           the American Statistical Association* Vol. 72, No. 358, pp.
           309--315, 1977.

    .. [3] SAS Institute, Inc., "The FREQ Procedure (Book Excerpt)",
           *SAS/STAT 9.2 User's Guide, Second Edition*, SAS Publishing, 2009.

    .. [4] Laerd Statistics, "Somers' d using SPSS Statistics", *SPSS
           Statistics Tutorials and Statistical Guides*,
           https://statistics.laerd.com/spss-tutorials/somers-d-using-spss-statistics.php,
           Accessed July 31, 2020.

    Examples
    --------
    We calculate Somers' D for the example given in [4]_, in which a hotel
    chain owner seeks to determine the association between hotel room
    cleanliness and customer satisfaction. The independent variable, hotel
    room cleanliness, is ranked on an ordinal scale: "below average (1)",
    "average (2)", or "above average (3)". The dependent variable, customer
    satisfaction, is ranked on a second scale: "very dissatisfied (1)",
    "moderately dissatisfied (2)", "neither dissatisfied nor satisfied (3)",
    "moderately satisfied (4)", or "very satisfied (5)". 189 customers
    respond to the survey, and the results are cast into a contingency table
    with the hotel room cleanliness as the "row" variable and customer
    satisfaction as the "column" variable.

    +-----+-----+-----+-----+-----+-----+
    |     | (1) | (2) | (3) | (4) | (5) |
    +=====+=====+=====+=====+=====+=====+
    | (1) | 27  | 25  | 14  | 7   | 0   |
    +-----+-----+-----+-----+-----+-----+
    | (2) | 7   | 14  | 18  | 35  | 12  |
    +-----+-----+-----+-----+-----+-----+
    | (3) | 1   | 3   | 2   | 7   | 17  |
    +-----+-----+-----+-----+-----+-----+

    For example, 27 customers assigned their room a cleanliness ranking of
    "below average (1)" and a corresponding satisfaction of "very
    dissatisfied (1)". We perform the analysis as follows.

    >>> from scipy.stats import somersd
    >>> table = [[27, 25, 14, 7, 0], [7, 14, 18, 35, 12], [1, 3, 2, 7, 17]]
    >>> res = somersd(table)
    >>> res.statistic
    0.6032766111513396
    >>> res.pvalue
    1.0007091191074533e-27

    The value of the Somers' D statistic is approximately 0.6, indicating
    a positive correlation between room cleanliness and customer satisfaction
    in the sample.
    The *p*-value is very small, indicating a very small probability of
    observing such an extreme value of the statistic under the null
    hypothesis that the statistic of the entire population (from which
    our sample of 189 customers is drawn) is zero. This supports the
    alternative hypothesis that the true value of Somers' D for the population
    is nonzero.
    """
    x, y = np.array(x), np.array(y)
    if x.ndim == 1:
        if x.size != y.size:
            raise ValueError("Rankings must be of equal length.")
        table = scipy.stats.contingency.crosstab(x, y)[1]
    elif x.ndim == 2:
        if np.any(x < 0):
            raise ValueError("All elements of the contingency table must be "
                             "non-negative.")
        if np.any(x != x.astype(int)):
            raise ValueError("All elements of the contingency table must be "
                             "integer.")
        if x.nonzero()[0].size < 2:
            raise ValueError("At least two elements of the contingency table "
                             "must be nonzero.")
        table = x
    else:
        raise ValueError("x must be either a 1D or 2D array")
    d, p = _somers_d(table)
    return SomersDResult(d, p, table)
def _pval_cvm_2samp_exact(s, nx, ny):
    """
    Compute the exact p-value of the Cramer-von Mises two-sample test
    for a given value s (float) of the test statistic by enumerating
    all possible combinations. nx and ny are the sizes of the samples.
    """
    z = np.arange(1, nx+ny+1)
    rangex = np.arange(1, nx+1)
    rangey = np.arange(1, ny+1)

    us = []
    # for the first sample x, select all possible combinations of
    # the ranks of the elements of x in the combined sample
    # note that the acutal values of the samples are irrelevant
    # once the ranks of the elements of x are fixed, the
    # ranks of the elements in the second sample
    # that z = 1, ..., nx+ny contains all possible ranks for a loop over all
    for c in combinations(z, nx):
        x = np.array(c)
        # the ranks of the second sample y are given by the set z - x
        # note than one needs to shift by -1 since the lowest rank is 1
        mask = np.ones(nx+ny, bool)
        mask[x-1] = False
        y = z[mask]
        # compute the statistic
        u = nx * np.sum((x - rangex)**2)
        u += ny * np.sum((y - rangey)**2)
        us.append(u)
    # compute the values of u and the frequencies
    u, cnt = np.unique(us, return_counts=True)
    return np.sum(cnt[u >= s]) / np.sum(cnt)


def cramervonmises_2samp(x, y, method='auto'):
    """
    Perform the two-sample Cramér-von Mises test for goodness of fit.

    This is the two-sample version of the Cramér-von Mises test ([1]_):
    for two independent samples :math:`X_1, ..., X_n` and
    :math:`Y_1, ..., Y_m`, the null hypothesis is that the samples
    come from the same (unspecified) continuous distribution.

    Parameters
    ----------
    x : array_like
        A 1-D array of observed values of the random variables :math:`X_i`.
    y : array_like
        A 1-D array of observed values of the random variables :math:`Y_i`.
    method : {'auto', 'asymptotic', 'exact'}, optional
        The method used to compute the p-value, see Notes for details.
        The default is 'auto'.

    Returns
    -------
    res : object with attributes
        statistic : float
            Cramér-von Mises statistic.
        pvalue : float
            The p-value.

    See Also
    --------
    cramervonmises, anderson_ksamp, epps_singleton_2samp, ks_2samp

    Notes
    -----
    .. versionadded:: 1.7.0

    The statistic is computed according to equation 9 in [2]_. The
    calculation of the p-value depends on the keyword `method`:

    - ``asymptotic``: The p-value is approximated by using the limiting
      distribution of the test statistic.
    - ``exact``: The exact p-value is computed by enumerating all
      possible combinations of the test statistic, see [2]_.

    The exact calculation will be very slow even for moderate sample
    sizes as the number of combinations increases rapidly with the
    size of the samples. If ``method=='auto'``, the exact approach
    is used if both samples contain less than 10 observations,
    otherwise the asymptotic distribution is used.

    If the underlying distribution is not continuous, the p-value is likely to
    be conservative (Section 6.2 in [3]_). When ranking the data to compute
    the test statistic, midranks are used if there are ties.

    References
    ----------
    .. [1] https://en.wikipedia.org/wiki/Cramer-von_Mises_criterion
    .. [2] Anderson, T.W. (1962). On the distribution of the two-sample
           Cramer-von-Mises criterion. The Annals of Mathematical
           Statistics, pp. 1148-1159.
    .. [3] Conover, W.J., Practical Nonparametric Statistics, 1971.

    Examples
    --------

    Suppose we wish to test whether two samples generated by
    ``scipy.stats.norm.rvs`` have the same distribution. We choose a
    significance level of alpha=0.05.

    >>> from scipy import stats
    >>> np.random.seed(626)
    >>> x = stats.norm.rvs(size=100)
    >>> y = stats.norm.rvs(size=70)
    >>> res = stats.cramervonmises_2samp(x, y)
    >>> res.statistic, res.pvalue
    (0.24331932773109344, 0.19815666195647663)

    The p-value exceeds our chosen significance level, so we do not
    reject the null hypothesis that the observed samples are drawn from the
    same distribution.

    For small sample sizes, one can compute the exact p-values:

    >>> x = stats.norm.rvs(size=7)
    >>> y = stats.t.rvs(df=2, size=6)
    >>> res = stats.cramervonmises_2samp(x, y, method='exact')
    >>> res.statistic, res.pvalue
    (0.2655677655677655, 0.18706293706293706)

    The p-value based on the asymptotic distribution is a good approximation
    even though the sample size is small.

    >>> res = stats.cramervonmises_2samp(x, y, method='asymptotic')
    >>> res.statistic, res.pvalue
    (0.2655677655677655, 0.17974247316290415)

    Independent of the method, one would not reject the null hypothesis at the
    chosen significance level in this example.
    """

    xa = np.sort(np.asarray(x))
    ya = np.sort(np.asarray(y))

    if xa.size <= 1 or ya.size <= 1:
        raise ValueError('x and y must contain at least two observations.')
    if xa.ndim > 1 or ya.ndim > 1:
        raise ValueError('The samples must be one-dimensional.')
    if method not in ['auto', 'exact', 'asymptotic']:
        raise ValueError('method must be either auto, exact or asymptotic.')

    nx = len(xa)
    ny = len(ya)

    if method == 'auto':
        if max(nx, ny) > 10:
            method = 'asymptotic'
        else:
            method = 'exact'

    # get ranks of x and y in the pooled sample
    z = np.concatenate([xa, ya])
    # in case of ties, use midrank (see [1])
    r = scipy.stats.rankdata(z, method='average')
    rx = r[:nx]
    ry = r[nx:]

    # compute U (eq. 10 in [2])
    u = nx * np.sum((rx - np.arange(1, nx+1))**2)
    u += ny * np.sum((ry - np.arange(1, ny+1))**2)

    # compute T (eq. 9 in [2])
    k, N = nx*ny, nx + ny
    t = u / (k*N) - (4*k - 1)/(6*N)

    if method == 'exact':
        p = _pval_cvm_2samp_exact(u, nx, ny)
    else:
        # compute expected value and variance of T (eq. 11 and 14 in [2])
        et = (1 + 1/N)/6
        vt = (N+1) * (4*k*N - 3*(nx**2 + ny**2) - 2*k)
        vt = vt / (45 * N**2 * 4 * k)

        # computed the normalized statistic (eq. 15 in [2])
        tn = 1/6 + (t - et) / np.sqrt(45 * vt)

        # approximate distribution of tn with limiting distribution
        # of the one-sample test statistic
        # if tn < 0.003, the _cdf_cvm_inf(tn) < 1.28*1e-18, return 1.0 directly
        if tn < 0.003:
            p = 1.0
        else:
            p = max(0, 1. - _cdf_cvm_inf(tn))

    return CramerVonMisesResult(statistic=t, pvalue=p)
