// Spike custom strategy: reverse the declared target order.
export default {
  order(targets, deps) {
    deps.log?.info?.("COMBO", `[spike-reverse] custom ordering: ${targets.map((t) => t.modelStr).reverse().join(" > ")}`);
    return targets.reverse();
  },
};
