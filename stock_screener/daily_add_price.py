import sys
from update_excel import update_excel


if __name__ == "__main__":

    target_date = None

    if len(sys.argv) > 1:
        target_date = sys.argv[1]

    update_excel(target_date)
