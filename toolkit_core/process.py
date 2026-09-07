"""Read process identity without platform-specific ps syntax."""
import json
import sys
import psutil

if __name__ == '__main__':
    try:
        process = psutil.Process(int(sys.argv[1]))
        print(json.dumps({'created': process.create_time(), 'argv': process.cmdline()}))
    except (psutil.Error, ValueError):
        print('null')
