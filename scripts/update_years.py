import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "patristics.db"

YEAR_MAP: dict[str, int] = {
    # Patristic primary sources
    "manuscripts/ccel_thml/philo/works.txt": 40,
    "manuscripts/ccel_thml/irenaeus/demonstr.txt": 180,  # fix from 1920
    "manuscripts/ccel_thml/origen/prayer.txt": 233,
    "manuscripts/ccel_thml/athanasius/incarnation.txt": 318,
    "manuscripts/ccel_thml/augustine/confess.txt": 397,
    "manuscripts/ccel_thml/augustine/confessions.txt": 397,
    "manuscripts/ccel_thml/augustine/doctrine.txt": 397,
    "manuscripts/ccel_thml/augustine/enchiridion.txt": 421,
    "manuscripts/ccel_thml/athanasius/paradise1.txt": 420,
    "manuscripts/ccel_thml/athanasius/paradise2.txt": 420,
    "manuscripts/ccel_thml/cassian/conferences.txt": 420,
    "manuscripts/ccel_thml/dionysius/celestial.txt": 500,
    "manuscripts/ccel_thml/dionysius/works.txt": 500,
    "manuscripts/ccel_thml/gregory/life_rule.txt": 593,

    # Schaff ANF series
    "manuscripts/ccel_thml/schaff/anf01.txt": 96,
    "manuscripts/ccel_thml/schaff/anf02.txt": 150,
    "manuscripts/ccel_thml/schaff/anf03.txt": 200,
    "manuscripts/ccel_thml/schaff/anf04.txt": 230,
    "manuscripts/ccel_thml/schaff/anf05.txt": 250,
    "manuscripts/ccel_thml/schaff/anf06.txt": 250,
    "manuscripts/ccel_thml/schaff/anf07.txt": 305,
    "manuscripts/ccel_thml/schaff/anf08.txt": 300,
    "manuscripts/ccel_thml/schaff/anf09.txt": 226,
    "manuscripts/ccel_thml/schaff/anf10.txt": 1896,

    # Schaff NPNF1 series
    "manuscripts/ccel_thml/schaff/npnf101.txt": 397,
    "manuscripts/ccel_thml/schaff/npnf102.txt": 413,
    "manuscripts/ccel_thml/schaff/npnf103.txt": 400,
    "manuscripts/ccel_thml/schaff/npnf104.txt": 388,
    "manuscripts/ccel_thml/schaff/npnf105.txt": 412,
    "manuscripts/ccel_thml/schaff/npnf106.txt": 393,
    "manuscripts/ccel_thml/schaff/npnf107.txt": 407,
    "manuscripts/ccel_thml/schaff/npnf108.txt": 392,
    "manuscripts/ccel_thml/schaff/npnf109.txt": 386,
    "manuscripts/ccel_thml/schaff/npnf110.txt": 390,
    "manuscripts/ccel_thml/schaff/npnf111.txt": 391,
    "manuscripts/ccel_thml/schaff/npnf112.txt": 392,
    "manuscripts/ccel_thml/schaff/npnf113.txt": 394,
    "manuscripts/ccel_thml/schaff/npnf114.txt": 391,

    # Schaff NPNF2 series
    "manuscripts/ccel_thml/schaff/npnf201.txt": 313,
    "manuscripts/ccel_thml/schaff/npnf202.txt": 448,
    "manuscripts/ccel_thml/schaff/npnf203.txt": 395,
    "manuscripts/ccel_thml/schaff/npnf204.txt": 318,
    "manuscripts/ccel_thml/schaff/npnf205.txt": 380,
    "manuscripts/ccel_thml/schaff/npnf206.txt": 393,
    "manuscripts/ccel_thml/schaff/npnf207.txt": 350,
    "manuscripts/ccel_thml/schaff/npnf208.txt": 370,
    "manuscripts/ccel_thml/schaff/npnf209.txt": 350,
    "manuscripts/ccel_thml/schaff/npnf210.txt": 374,
    "manuscripts/ccel_thml/schaff/npnf211.txt": 400,
    "manuscripts/ccel_thml/schaff/npnf212.txt": 445,
    "manuscripts/ccel_thml/schaff/npnf213.txt": 350,
    "manuscripts/ccel_thml/schaff/npnf214.txt": 325,

    # Medieval works
    "manuscripts/ccel_thml/bernard/loving_god.txt": 1132,
    "manuscripts/ccel_thml/bernard/letters.txt": 1130,
    "manuscripts/ccel_thml/bernard/st_malachy.txt": 1149,
    "bernard_st_malachy.txt": 1149,
    "manuscripts/ccel_thml/anselm/basic_works.txt": 1078,
    "manuscripts/ccel_thml/anselm/meditations.txt": 1100,
    "manuscripts/ccel_thml/anselm/devotions.txt": 1100,
    "manuscripts/ccel_thml/aquinas/gentiles.txt": 1258,
    "manuscripts/ccel_thml/aquinas/catena1.txt": 1263,
    "manuscripts/ccel_thml/aquinas/catena2.txt": 1263,
    "manuscripts/ccel_thml/aquinas/nature_grace.txt": 1260,
    "manuscripts/ccel_thml/aquinas/summa.txt": 1265,
    "manuscripts/ccel_thml/bonaventure/mindsroad.txt": 1259,
    "manuscripts/ccel_thml/rolle/fire.txt": 1343,  # fix from 1914
    "manuscripts/ccel_thml/tauler/inner_way.txt": 1340,  # fix from 1901
    "manuscripts/ccel_thml/tauler/following.txt": 1350,
    "manuscripts/ccel_thml/tauler/meditations.txt": 1350,
    "manuscripts/ccel_thml/eckhart/sermons.txt": 1305,
    "manuscripts/ccel_thml/eckhart/mystische.txt": 1305,
    "manuscripts/ccel_thml/pfeiffer/eckhart1.txt": 1305,
    "manuscripts/ccel_thml/anonymous2/cloud.txt": 1375,
    "manuscripts/ccel_thml/hilton/ladder.txt": 1388,
    "manuscripts/ccel_thml/hilton/angels.txt": 1388,
    "manuscripts/ccel_thml/hilton/treatise.txt": 1390,
    "manuscripts/ccel_thml/kempis/imitation.txt": 1418,  # fix from 1998
    "manuscripts/ccel_thml/kempis/founders.txt": 1425,

    # Early modern works
    "manuscripts/ccel_thml/ignatius/exercises.txt": 1548,
    "manuscripts/ccel_thml/ignatius/autobiography.txt": 1555,
    "manuscripts/ccel_thml/guyon/spiritual_torrents.txt": 1682,
    "guyon_spiritual_torrents.txt": 1682,
    "manuscripts/ccel_thml/guyon/prayer.txt": 1685,
    "manuscripts/ccel_thml/guyon/auto.txt": 1688,
    "manuscripts/ccel_thml/guyon/song.txt": 1688,
    "manuscripts/ccel_thml/fenelon/maxims.txt": 1697,
    "manuscripts/ccel_thml/fenelon/existence_god.txt": 1712,
    "fenelon_existence_god.txt": 1712,
    "manuscripts/ccel_thml/fenelon/progress.txt": 1877,
    "manuscripts/ccel_thml/lawrence/practice.txt": 1692,
    "manuscripts/ccel_thml/baker/more.txt": 1657,
    "manuscripts/ccel_thml/law/apracticaltreat.txt": 1726,
    "law_apracticaltreat.txt": 1726,
    "manuscripts/ccel_thml/law/serious_call.txt": 1729,
    "manuscripts/ccel_thml/law/humbleearnest.txt": 1761,
    "law_humbleearnest.txt": 1761,
    "manuscripts/ccel_thml/law/love2.txt": 1752,
    "manuscripts/ccel_thml/law/prayer.txt": 1749,
    "manuscripts/ccel_thml/law/collection.txt": 1760,
    "manuscripts/ccel_thml/law/errors.txt": 1740,
    "manuscripts/ccel_thml/law/doubt.txt": 1740,
    "manuscripts/ccel_thml/law/clergy.txt": 1761,
    "manuscripts/ccel_thml/law/grounds.txt": 1739,
    "manuscripts/ccel_thml/law/waytodivine.txt": 1752,

    # Modern scholarly works
    "manuscripts/ccel_thml/feltoe/dionysius.txt": 1904,
    "manuscripts/ccel_thml/richardson/fathers.txt": 1953,
    "manuscripts/ccel_thml/potts/prayerearly.txt": 1953,
    "manuscripts/ccel_thml/leightonpullan/earlychristian.txt": 1898,
    "manuscripts/ccel_thml/lightfoot/fathers.txt": 1891,
    "manuscripts/ccel_thml/lake/fathers2.txt": 1912,
    "manuscripts/ccel_thml/brownlie/earlyhymns.txt": 1907,
    "manuscripts/ccel_thml/brownlie/latinhymns.txt": 1907,
    "manuscripts/ccel_thml/deane/pseudepig.txt": 1891,
    "manuscripts/ccel_thml/tolstoy/gospel.txt": 1896,
    "manuscripts/ccel_thml/bacon_lw/history.txt": 1897,
    "manuscripts/ccel_thml/scrivener/ntcrit1.txt": 1883,
    "manuscripts/ccel_thml/scrivener/ntcrit2.txt": 1883,
    "manuscripts/ccel_thml/farrar/clouds.txt": 1895,
    "manuscripts/ccel_thml/manning/wesleyhymns.txt": 1942,
}


def main():
    conn = sqlite3.connect(DB_PATH)
    updated = 0
    not_found = []
    for filename, year in YEAR_MAP.items():
        cur = conn.execute(
            "UPDATE manuscripts SET year = ? WHERE filename = ?",
            (year, filename)
        )
        if cur.rowcount:
            updated += 1
        else:
            not_found.append(filename)
    conn.commit()
    conn.close()
    print(f"Updated {updated} / {len(YEAR_MAP)} manuscripts")
    if not_found:
        print(f"\nNot found in DB ({len(not_found)}):")
        for f in not_found:
            print(f"  {f}")


if __name__ == "__main__":
    main()
