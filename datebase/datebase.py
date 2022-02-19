import sqlite3

class datebase:

    user = "default"
    guild = "default"
    search1 = "default"
    search2 = "default"
    search3 = "default"

    def start( self ):

        db = sqlite3.connect( "datebase/warns.db" )
        sql = db.cursor()
        global sql, db
        sql.execute( '''CREATE TABLE IF NOT EXISTS datebase (
            guild INT,
            user INT,
            warn INT
        )''')
        db.commit()

    def warn(self):
        sql.execute( f"SELECT guild FROM datebase WHERE guild = '{ self.guild }'" )
        if sql.fetchone() is None:
            sql.execute( f"INSERT INTO datebase VALUES (?,?,?,?)", ( self.guild, self.user, 1, 0 ) )
            db.commit()
        else:
            sql.execute( f"SELECT user FROM datebase WHERE guild = '{ self.guild }' AND user = '{ self.user }'" )
            if sql.fetchone() is None:
                sql.execute( f"INSERT INTO datebase VALUES (?,?,?,?)", ( self.guild, self.user, 1, 0 ) )
                db.commit()
            else:
                for i in sql.execute( f"SELECT warn FROM datebase WHERE guild = '{ self.guild }' AND user = '{self.user}'" ):
                    newWarn = i[0] + 1
                sql.execute( f"UPDATE datebase SET warn = '{ newWarn }' WHERE guild = '{ self.guild } AND user = '{ self.user }" )
                db.commit()

    def warns_list( self ):
        self.search1 = ""
        self.search2 = ""
        self.search3 = ""
        for i in sql.execute( "SELECT guild FROM datebase" ):
            self.search1 += f"\n{ i[0] }"
        for i in sql.execute( "SELECT user FROM datebase" ):
            self.search2 += f"\n{ i[0] }"
        for i in sql.execute( "SELECT warn FROM datebase" ):
            self.search3 += f"\n{ i[0] }"
