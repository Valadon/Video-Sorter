if __name__ == '__main__':
    import os
    import datetime

    filepath = input('Path: ')
    dt = datetime.datetime.strptime(input('Date: '), '%m/%d/%Y, %H:%M:%S')
    os.utime(filepath, (dt.timestamp(), dt.timestamp()))