import pandas as pd
import os


FILE="data/range_history.xlsx"


def update_excel(result):


    os.makedirs(
        "data",
        exist_ok=True
    )


    if os.path.exists(FILE):

        df=pd.read_excel(FILE)

    else:

        df=pd.DataFrame(
            columns=["name"]
        )



    new=pd.DataFrame(result)



    today=new.columns[-1]



    # 기존 날짜 삭제 후 재작성

    if today in df.columns:

        df.drop(
            columns=[today],
            inplace=True
        )


    df=df.merge(
        new,
        on="name",
        how="outer"
    )


    df.to_excel(
        FILE,
        index=False
    )