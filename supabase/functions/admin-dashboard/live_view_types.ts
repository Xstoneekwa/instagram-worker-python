export type RestClient = {
  select: (
    tableName: string,
    query: Record<string, string>,
  ) => Promise<Response>;
  insert: (
    tableName: string,
    body: Record<string, unknown>,
  ) => Promise<Response>;
  update: (
    tableName: string,
    query: Record<string, string>,
    body: Record<string, unknown>,
  ) => Promise<Response>;
};
