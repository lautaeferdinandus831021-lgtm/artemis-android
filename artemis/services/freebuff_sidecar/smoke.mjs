/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { CodebuffClient } from '@codebuff/sdk'

const apiKey = process.env.FB_KEY
if (!apiKey) {
  console.error('FB_KEY not set')
  process.exit(2)
}

const client = new CodebuffClient({ apiKey, cwd: '/tmp/fb-sidecar-cwd' })

const connected = await client.checkConnection()
console.log('checkConnection:', connected)

const run = await client.run({
  agent: 'freebuff-bridge-smoke',
  agentDefinitions: [
    {
      id: 'freebuff-bridge-smoke',
      model: 'meta/muse-spark-1.3-contributor',
      displayName: 'Bridge smoke',
      toolNames: [],
      instructionsPrompt:
        'You are an echo endpoint for an automated test. Reply with exactly the requested token and nothing else.',
      outputMode: 'last_message',
    },
  ],
  prompt: 'Reply with exactly: PONG',
  maxAgentSteps: 2,
  handleEvent: (event) => {
    if (event && event.type === 'error') {
      console.log('ERR-EVENT:', JSON.stringify(event))
    }
  },
})

console.log('stopReason:', run.stopReason)
console.log('output:', typeof run.output === 'string' ? run.output : JSON.stringify(run.output))
