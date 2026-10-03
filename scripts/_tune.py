import importlib
import random
import sys
from collections import Counter

sys.path.insert(0, "scripts")
import generate_synthetic_dataset as g


def build(plan_scale, occ_n, occ_w, reg_n, reg_w, home_w):
    g.PLAN_SCALE = plan_scale
    g.OCC_RANGE = occ_n
    g.OCC_W = occ_w
    g.REG_N = reg_n
    g.REG_W = reg_w
    g.HOME_W = home_w
    importlib.reload(g)
    r = lambda n: random.Random(n)
    merchants = g.build_merchants(r(g.SEED + 1))
    cohorts = g.assign_cohorts()
    customers = g.build_customers(merchants, cohorts, r(g.SEED + 3))
    txns = g.generate_transactions(customers, merchants, r(g.SEED + 4))
    idx = g.PairIndex(txns)
    pairs = list(idx.pairs())
    cnt = Counter(m for _, m in pairs)
    factory = g.OfferFactory(r(g.SEED + 6))
    g.build_offers(merchants, txns, factory)
    campaigns = g.build_campaigns(merchants, factory, idx, r(g.SEED + 7))
    per_rule = {}
    for camp in campaigns:
        rule = camp["_rule"]
        as_of = camp["_eval_as_of"]
        mid = camp["merchant_id"]
        pool = [c for c, m in idx.pairs(mid)
                if g.qualifies(idx, c, mid, as_of, rule)]
        if rule["rule"] == "TOP_SPENDERS":
            allowed = g.percentile_allow(idx, mid, as_of,
                                         rule["spend_percentile_min"])
            pool = [c for c in pool if c in allowed]
        per_rule.setdefault(rule["rule"], []).append(len(pool))
    print("scale=%.2f occ=%s occ_w=%s reg=%s reg_w=%s home=%s" %
          (plan_scale, occ_n, occ_w, reg_n, reg_w, home_w))
    print("  txns=%d  pairs=%d  pairs/merchant avg=%.0f (min %d max %d)" %
          (len(txns), len(pairs), len(pairs) / 20, min(cnt.values()),
           max(cnt.values())))
    total = 0
    n_camp = 0
    for rule, sizes in per_rule.items():
        avg = sum(sizes) / len(sizes)
        total += sum(sizes)
        n_camp += len(sizes)
        print("   %-18s n=%2d pool avg=%5.1f max=%4d" % (rule, len(sizes), avg,
                                                         max(sizes)))
    print("  campaigns=%d  sum(pool)=%d  avg pool=%.0f" %
          (n_camp, total, total / max(n_camp, 1)))
    return txns, pairs


if __name__ == "__main__":
    build(1.0, (4, 8), (0.008, 0.030), (2, 4), (0.08, 0.15), (0.52, 0.66))
    build(1.35, (6, 10), (0.030, 0.070), (2, 4), (0.09, 0.16), (0.42, 0.55))
    build(1.45, (7, 11), (0.040, 0.080), (3, 5), (0.09, 0.16), (0.40, 0.52))
