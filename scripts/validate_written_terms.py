"""New paid run; prior ten-rule task and exports remain untouched."""
from scripts import validate_ten_rules as runner

runner.MARKER = runner.ROOT/'work/written-terms-acceptance-task.json'
runner.OUTPUT_PREFIX = 'written-terms'

if __name__ == '__main__':
    runner.main()
