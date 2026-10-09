#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

function fail(reason) {
  if (process.argv.slice(2).includes('--json')) {
    console.log(JSON.stringify({schema: 'taskclosurekit/result/v2', operation: 'unknown',
      operational: {status: 'environment_error'}, task_id: null, state: 'INVALID',
      decision: 'NOT_EVALUATED', reasons: [reason], freshness: [], claim: null,
      next_action: 'none'}));
  } else {
    console.error('TaskClosureKit: ' + reason + '. Requires POSIX, Node >=22, Python >=3.9 and system Git. ' +
      'Set TASKCLOSUREKIT_PYTHON to an absolute Python executable if needed.');
  }
  process.exitCode = 3;
}

function pythonExecutable() {
  const override = process.env.TASKCLOSUREKIT_PYTHON;
  let candidate;
  if (override !== undefined) {
    if (!path.isAbsolute(override)) throw new Error('invalid_python_override');
    candidate = override;
  } else {
    candidate = ['/usr/bin/python3', '/bin/python3'].find(filename => fs.existsSync(filename));
    if (!candidate) throw new Error('python_unavailable');
  }
  try {
    const executable = fs.realpathSync(candidate);
    if (!fs.statSync(executable).isFile()) throw new Error();
    fs.accessSync(executable, fs.constants.X_OK);
    return executable;
  } catch (_) {
    throw new Error(override === undefined ? 'python_unavailable' : 'invalid_python_override');
  }
}

if (!['darwin', 'linux'].includes(process.platform)) {
  fail('unsupported_platform');
} else if (Number(process.versions.node.split('.')[0]) < 22) {
  fail('unsupported_node_version');
} else {
  let executable;
  try { executable = pythonExecutable(); } catch (error) { fail(error.message); }
  if (executable) {
    // A separate session receives each forwarded terminal signal once; inherited TTY
    // descriptors still serve operator confirmation, and the child remains awaited.
    const child = spawn(executable, ['-I', '-S', '-B', path.join(__dirname, '_npm_bootstrap.py'),
      ...process.argv.slice(2)], {stdio: 'inherit', shell: false, detached: true});
    const forward = signal => { if (child.exitCode === null && child.signalCode === null) child.kill(signal); };
    process.on('SIGINT', forward);
    process.on('SIGTERM', forward);
    let launchFailed = false;
    child.on('error', () => { launchFailed = true; fail('python_launch_failed'); });
    child.on('close', (code, signal) => {
      process.removeListener('SIGINT', forward);
      process.removeListener('SIGTERM', forward);
      if (launchFailed) return;
      process.exitCode = code === null ? (signal === 'SIGINT' ? 130 : signal === 'SIGTERM' ? 143 : 3) : code;
    });
  }
}
