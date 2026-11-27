using System;

public static class Sessions
{
    public static string NewId() => Guid.NewGuid().ToString();
}
