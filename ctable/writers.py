import logging
import six
import sqlalchemy
import alembic

logger = logging.getLogger(__name__)


class SqlTableWriter(object):
    """
    Write tables to a database specified by URL
    (TODO) with "upsert" based on primary key.
    """

    def __init__(self, url_or_connection, sql_extract):
        if isinstance(url_or_connection, six.string_types):
            self.base_connection = sqlalchemy.create_engine(url_or_connection)
        else:
            self.base_connection = url_or_connection
        self.sql_extract = sql_extract

    def __enter__(self):
        self.connection = self.base_connection.connect()  # "forks" the SqlAlchemy connection
        return self  # TODO: A safe context manager so this can be called many times

    def __exit__(self, type, value, traceback):
        self.connection.close()

    @property
    def metadata(self):
        if not hasattr(self, '_metadata'):
            self._metadata = sqlalchemy.MetaData()
            self._metadata.bind = self.connection
            self._metadata.reflect()
        return self._metadata

    def table(self, table_name):
        return sqlalchemy.Table(table_name, self.metadata, autoload=True, autoload_with=self.connection)

    def init_table(self, table_name, column_defs):
        ctx = alembic.migration.MigrationContext.configure(self.connection)
        op = alembic.operations.Operations(ctx)

        if not table_name in self.metadata.tables:
            columns = []
            for c in column_defs:
                columns.append(c.sql_column)

            op.create_table(table_name, *columns)
            self.metadata.reflect()
        else:
            self.make_table_compatible(table_name, column_defs)

    def make_table_compatible(self, table_name, column_defs):
        # FIXME: This does lots of redundant checks in a tight loop. Stop doing that.

        ctx = alembic.migration.MigrationContext.configure(self.connection)
        op = alembic.operations.Operations(ctx)

        if not table_name in self.metadata.tables:
            raise Exception("Table does not exist", table_name)

        for column in column_defs:
            if not column in [c.name for c in self.table(table_name).columns]:
                op.add_column(table_name, column.sql_column)
                self.metadata.clear()
                self.metadata.reflect()

            else:
                columns = dict([(c.name, c) for c in self.table(table_name).columns])
                current_ty = columns[column].type

                if current_ty != column.sql_type:
                    raise Exception("Column types don't match", column.name)

    def upsert(self, table, row_dict, key_columns):

        # For atomicity "insert, catch, update" is slightly better than "select, insert or update".
        # The latter may crash, while the former may overwrite data (which should be fine if whatever is
        # racing against this is importing from the same source... if not you are busted anyhow
        try:
            insert = table.insert().values(**row_dict)
            self.connection.execute(insert)
        except sqlalchemy.exc.IntegrityError:
            update = table.update()
            for k in key_columns:
                k_val = row_dict.pop(k)
                update = update.where(getattr(table.c, k) == k_val)
            update = update.values(**row_dict)
            self.connection.execute(update)

    def write_table(self, table_name, rows):
        columns = self.sql_extract.columns
        key_columns = self.sql_extract.key_columns

        self.init_table(table_name, columns)

        for row_dict in rows:
            logger.debug(".")

            self.upsert(self.table(table_name), row_dict, key_columns)

        logger.debug("\n")
