#!/usr/bin/env python3
"""
Safe catalog regeneration script.

Regenerates all bug catalogs with proper resource management:
- Sequential processing (one repo at a time)
- No parallel workers (avoids memory issues)
- Progress monitoring
- Cleanup between repos
"""

import gc
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bugs.catalog import generate_catalog


def regenerate_all():
    """Regenerate all catalogs safely."""

    repos = [
        ('humanize', '/Users/yuan/stylebench-data/original/humanize'),
        ('validators', '/Users/yuan/stylebench-data/original/validators'),
        ('python-markdown', '/Users/yuan/stylebench-data/original/python-markdown'),
        ('more-itertools', '/Users/yuan/stylebench-data/original/more-itertools'),
    ]

    output_dir = Path('/Users/yuan/stylebench-data/bugs')
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    for i, (repo_name, repo_path) in enumerate(repos):
        print(f'\n{"="*60}')
        print(f'[{i+1}/{len(repos)}] Generating {repo_name} catalog...')
        print(f'{"="*60}')

        start_time = time.time()

        try:
            catalog = generate_catalog(
                repo_path=repo_path,
                repo_name=repo_name,
                style='original',
                max_bugs=50,
                verbose=True,
                parallel=False,  # Sequential only - safer
                num_workers=1,   # Single worker
            )

            output_path = output_dir / f'{repo_name}-original.json'
            catalog.save(output_path)

            elapsed = time.time() - start_time
            results[repo_name] = {
                'bugs': len(catalog.bugs),
                'time': elapsed,
                'status': 'OK'
            }

            print(f'\n✓ Saved {len(catalog.bugs)} bugs to {output_path}')
            print(f'  Time: {elapsed:.1f}s')

        except Exception as e:
            elapsed = time.time() - start_time
            results[repo_name] = {
                'bugs': 0,
                'time': elapsed,
                'status': f'ERROR: {e}'
            }
            print(f'\n✗ Error: {e}')

        # Cleanup between repos
        gc.collect()

        # Brief pause to let system stabilize
        if i < len(repos) - 1:
            print('\nPausing 5s before next repo...')
            time.sleep(5)

    # Summary
    print(f'\n{"="*60}')
    print('SUMMARY')
    print(f'{"="*60}')

    total_bugs = 0
    total_time = 0

    for repo_name, info in results.items():
        status = '✓' if info['status'] == 'OK' else '✗'
        print(f'{status} {repo_name}: {info["bugs"]} bugs in {info["time"]:.1f}s')
        total_bugs += info['bugs']
        total_time += info['time']

    print(f'\nTotal: {total_bugs} bugs generated in {total_time:.1f}s')

    return results


if __name__ == '__main__':
    print('Catalog Regeneration Script')
    print('='*60)
    print('Settings:')
    print('  - Sequential processing (no parallel)')
    print('  - One repo at a time')
    print('  - 5s pause between repos')
    print('  - Max 50 bugs per catalog')
    print('='*60)

    regenerate_all()
