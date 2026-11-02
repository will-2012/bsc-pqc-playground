// Remote clients probe their configured RPC; the controller checks every validator via SSM.
export function validatorRpcs(config, remote = {}) {
  if (remote.rpc) return [remote.rpc];
  return remote.validators || Array.from({length: config.validators},
    (_, i) => `http://127.0.0.1:${config.rpc_port + 2*i}`);
}
