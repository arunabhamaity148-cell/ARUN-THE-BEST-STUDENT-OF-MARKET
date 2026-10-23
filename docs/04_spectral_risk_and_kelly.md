# 📐 04. Spectral Risk, Mantegna Clustering & Quarter-Kelly Sizing

> **Audience:** Jara quantitative portfolio risk management ebong position sizing-er institutional math shikhte chay.  
> **Language:** Benglish (Bengali explanation + Linear Algebra & Financial Physics).

---

## 1. What is the Absorption Ratio (AR)?

Kothao jokhon boro risk toiri hoy, shob asset-er dam eksathe ek dike move kora shuru kore. Eta ke bole **Systemic Contagion**.

Absorption Ratio (AR) prothom propose korechilen Kritzman et al. (2011) MIT-te. Eta measure kore:
> Portfolio-r total variance-er kotokhani percentage ekta single dominant factor (first principal eigenvector) absorb korche?

$$AR = \frac{\lambda_{\max}}{\sum_{i=1}^n \lambda_i} = \frac{\lambda_{\max}}{n}$$

- **High AR ($\ge 0.72$):** Market tightly coupled. Ekta boro systemic risk ache (e.g., market-wide flash crash). Ekhane diversification kono kaj kore na.
- **Low AR ($< 0.58$):** Market healthy ebong dispersed. Prottek coin nijer idiosyncratic order flow-te cholche.

---

## 2. Zero-Allocation Flat C-Array EWMA Covariance Tensor

Amra `numpy` bebohar korte parbo na karon 1GB RAM limit. Tai amra Python standard library-r `array.array('d')` die ekta **Flat 1D Upper-Triangular C-Array** baniyechi.

10-ti asset-er symmetric $10 \times 10$ covariance matrix-e unique entries thake:
$$\frac{n(n + 1)}{2} = \frac{10 \times 11}{2} = 55 \text{ floats}$$

55-ti 64-bit float RAM-e matro **440 bytes** jaiga ney!

2D index $(i, j)$ theke 1D index map korar formula:
$$\text{flat\_idx}(i, j) = i \cdot n - \frac{i(i - 1)}{2} + (j - i) \quad \text{for } j \ge i$$

Prottek 1-minute kline close-e log-return $r_t = \ln(C_t / C_{t-1})$ calculate kore EWMA update kora hoy ($\alpha = 2 / (60 + 1)$):
$$\Delta_i = r_i - \mu_i, \quad \mu_i \leftarrow \mu_i + \alpha \Delta_i$$
$$\text{Cov}_{ij} \leftarrow (1 - \alpha) \cdot \left(\text{Cov}_{ij} + \alpha \Delta_i \Delta_j\right)$$

---

## 3. Power Iteration: Dominant Eigenvalue Without NumPy

Matrix-er largest eigenvalue $\lambda_{\max}$ ebong dominant eigenvector $v$ khujar jonno amra pure Python-e **Power Iteration Method** implement korechi.

### Algorithm:
1. Initialize vector $v$ with previous eigenvector estimate.
2. 8-ti iteration run koro:
   $$w_i = \sum_{j=0}^{n-1} \text{Corr}_{ij} \cdot v_j$$
   $$v \leftarrow \frac{w}{\|w\|_2}$$
3. Rayleigh Quotient die $\lambda_{\max}$ ber koro:
   $$\lambda_{\max} = \sum_{i=0}^{n-1} v_i \sum_{j=0}^{n-1} \text{Corr}_{ij} \cdot v_j$$
4. Absorption Ratio calculate koro:
   $$AR = \min\left(1.0, \max\left(\frac{1}{n}, \frac{\lambda_{\max}}{n}\right)\right)$$

Eta matro **0.02 millisecond**-e pure C-speed-e converge kore!

---

## 4. Mantegna Angular Distance (Cluster Collision Veto)

Jodi amader bot already `SOLUSDT`-te Long position nie thake, ebong ekhon `AVAXUSDT`-teo Long signal ashe, bot ki dujonkei buy korbe?

Na! Karon Solana ar Avalanche dujonei high-beta Layer-1 coin. Dujonke eksathe hold kora mane risk double kora.

Amra Mantegna (1999) metric distance calculate kori:
$$d_{ij} = \sqrt{\max\left(0.0, \frac{1 - \rho_{ij}^{\text{directional}}}{2}\right)}$$
$$\text{where } \rho_{ij}^{\text{directional}} = \text{dir}_i \cdot \text{dir}_j \cdot \text{Corr}_{ij}$$

- Jodi $d_{ij} \le 0.35$ hoy, er mane duiti asset cluster-e khub kachakachi (Cluster Collision).
- Bot second trade-ti **veto kore debe** portfolio balance banchate!

---

## 5. Quarter-Kelly Position Sizing

John Kelly-r (1956) famous formula investment growth maximize korar jonno bebohar kora hoy. Kintu full Kelly crypto futures-e khub volatile. Tai quantitative hedge fund-ra **Quarter-Kelly ($0.25 \times \text{Kelly}$)** bebohar kore.

### Formula:
$$f^* = \min\left(0.0125, 0.25 \times \left(p - \frac{1 - p}{b}\right)\right)$$
- $p$: Bayesian Win Probability from Signal Synthesizer ($p \ge 0.820$).
- $b$: Payoff ratio parameter ($b = 2.45$, based on $1.5R, 2.8R, 4.5R$ targets).
- Max portfolio risk cap: $1.25\%$ per trade.

### Eigen-Beta Risk Scaling:
Jodi market-e Absorption Ratio bere jay ba coin-tir market loading beshi hoy:
$$\text{Eigen Loading} = n \cdot v_i^2$$
$$\text{Risk Mult} = \max(0.25, \min(1.0, 1.0 - 0.55 \cdot AR \cdot \text{Eigen Loading}))$$
$$\text{Final Kelly Fraction} = f^* \times \text{Risk Mult}$$

Jokhon market stress-e thake, bot automatically position size **1.25% theke komie 0.45%-e nambie aane**, capital protection guarantee korar jonno!
