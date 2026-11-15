"""End-to-end demonstration over real HTTP.

The script starts the actual service on an ephemeral port, consumes it through
app.client (the same consumer used by the business UI), and prints the input
basis, the analysis result and its limits.  It is not a replay of recorded
logs, and it does not sign, select or commit to a carrier.
"""

import json
import threading

from app.client import analysis, request
from app.server import make_server


def show(title, payload):
    print('--- ' + title)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    with make_server() as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            show('health', request(base, '/health'))
            show('cases', request(base, '/cases'))

            for name in request(base, '/cases')['cases']:
                show(name + ' preview (existing consumer route)',
                     request(base, '/preview', {'case': name}))

            standard = analysis(base, 'standard')
            show('standard analysis', standard)

            edge = analysis(base, 'edge')
            show('edge analysis (conflicting opinions)', edge)

            print('--- meeting follow-up: answering the first question for edge')
            first = edge['questions'][0]
            print('question asked: ' + first['prompt'])
            outcome = first['outcomes'][0]
            show('predicted effect of answering',
                 {'answer': outcome['answer'], 'changes': outcome['changes']})

            revised = analysis(base, 'edge', answers=[outcome['answer']])
            show('edge analysis after the answer', revised)

            print('--- summary')
            for name, result in [('standard', standard), ('edge', edge),
                                 ('edge+answer', revised)]:
                diagnostics = result['diagnostics']
                summary = {
                    'case': name,
                    'snapshot': result['snapshot'],
                    'possible_winners': result['possible_winners'],
                    'necessary_edges': [[row['better'], row['worse']]
                                        for row in result['necessary_edges']],
                    'conflicts': [[group['type'], group['preferences']]
                                  for group in result['conflicts']],
                    'questions': [[question['type'], question['alternatives']]
                                  for question in result['questions']],
                    'definitely_excluded': diagnostics['definitely_excluded'],
                    'conditional_exclusions': [row['alternative']
                                               for row in diagnostics['conditional_exclusions']],
                    'preferences_suspended': diagnostics['preferences_suspended'],
                    'preferences_set_aside': diagnostics['preferences_set_aside'],
                    'not_a_commitment': diagnostics['not_a_commitment'],
                }
                show('summary ' + name, summary)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()


if __name__ == '__main__':
    main()
